import os
import json
import hashlib
import pytest
from dotenv import load_dotenv
from web3 import Web3

# Ścieżka do skompilowanego artefaktu kontraktu (plik JSON generowany przez Foundry).
# os.path.dirname(__file__) daje nam katalog tego pliku testowego,
# dzięki czemu ścieżka działa niezależnie od tego, skąd uruchamiamy pytest.
_ARTIFACT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "contracts", "out",
    "AttestationRegistry.sol", "AttestationRegistry.json"
)


def _sign_and_send(w3, tx, private_key):
    """Podpisuje transakcję kluczem prywatnym, wysyła ją do sieci i czeka na potwierdzenie."""
    signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    # wait_for_transaction_receipt blokuje wykonanie aż blok zostanie wykopany (w Anvilu - natychmiastowo)
    return w3.eth.wait_for_transaction_receipt(tx_hash)


def _tx_params(w3, account, gas):
    """Zwraca słownik z parametrami transakcji: sieć, limit gazu, cena gazu i nonce."""
    return {
        'chainId': w3.eth.chain_id,          # ID sieci pobrane z węzła (np. 31337 dla Anvila)
        'gas': gas,                           # Maksymalna ilość gazu jaką możemy zużyć
        'gasPrice': w3.eth.gas_price,         # Aktualna cena gazu w sieci
        'nonce': w3.eth.get_transaction_count(account.address),  # Licznik wysłanych tx z tego adresu
    }


@pytest.fixture(scope="module")
def web3_setup():
    """
    Fixture uruchamiana raz na cały moduł testowy.
    Ładuje zmienne z pliku .env, nawiązuje połączenie z węzłem Anvil
    i konfiguruje konto admina na podstawie klucza prywatnego.
    """
    # Wczytujemy zmienne środowiskowe z pliku .env (RPC_URL, PRIVATE_KEY itp.)
    load_dotenv()

    rpc_url = os.getenv("RPC_URL", "http://127.0.0.1:8545")
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    assert w3.is_connected(), "Brak połączenia z lokalnym sandboxem Anvil!"

    # Pobieramy klucz prywatny admina z .env i tworzymy obiekt konta
    private_key = os.getenv("PRIVATE_KEY")
    account = w3.eth.account.from_key(private_key)

    # Zwracamy trójkę: obiekt Web3, konto i surowy klucz prywatny (potrzebny do podpisywania tx)
    return w3, account, private_key


@pytest.fixture(scope="module")
def contract_instance(web3_setup):
    """
    Fixture wdrażająca świeżą instancję kontraktu AttestationRegistry do Anvila.
    Uruchamiana raz na moduł - każdy test w tym pliku dostaje ten sam adres kontraktu.
    """
    w3, account, private_key = web3_setup

    # Ładujemy skompilowany artefakt kontraktu (ABI + bytecode) wygenerowany przez `forge build`
    with open(_ARTIFACT_PATH, "r") as f:
        artifact = json.load(f)

    abi = artifact["abi"]                       # Interfejs kontraktu (lista funkcji i zdarzeń)
    bytecode = artifact["bytecode"]["object"]   # Skompilowany kod bajtowy do wdrożenia

    # Tworzymy obiekt fabryki kontraktu - służy do zbudowania transakcji wdrożenia
    contract_factory = w3.eth.contract(abi=abi, bytecode=bytecode)

    # Budujemy, podpisujemy i wysyłamy transakcję wdrożenia (deploy)
    deploy_tx = contract_factory.constructor().build_transaction(_tx_params(w3, account, 1000000))
    tx_receipt = _sign_and_send(w3, deploy_tx, private_key)

    print(f"\n[QA LOG] Kontrakt pomyślnie wdrożony pod adres: {tx_receipt.contractAddress}")

    # Zwracamy gotową instancję połączoną z nowym, prawidłowym adresem kontraktu
    return w3.eth.contract(address=tx_receipt.contractAddress, abi=abi)


def test_e2e_blockchain_settlement_pipeline(web3_setup, contract_instance):
    """
    Pełen test E2E: Przetwarzanie danych -> Generowanie dowodu -> Zapis na Blockchainie.
    """
    w3, account, private_key = web3_setup

    # 1. Pipeline danych: symulujemy kanoniczną (ustandaryzowaną) postać transakcji finansowej.
    #    Dane są już w formacie kanonicznym - bez spacji, w ustalonej kolejności kluczy.
    canonical_data = '{"sender":"Bank_A","receiver":"Bank_B","amount":5000000}'

    # 2. Generowanie dowodu (State Root): skrót SHA-256 danych = 32-bajtowy "odcisk palca" transakcji.
    #    Ten hash będzie przechowywany na blockchainie jako niezmienialny dowód integralności.
    state_root = hashlib.sha256(canonical_data.encode('utf-8')).digest()
    mock_timestamp = 1717596000  # Przykładowy timestamp (klucz identyfikujący partię rozliczeniową)

    print(f"\n[QA LOG] Wygenerowany dowód danych: {state_root.hex()}")

    # 3. Budowanie transakcji blockchainowej - wywołujemy funkcję 'publishAttestation' z kontraktu
    transaction = contract_instance.functions.publishAttestation(
        mock_timestamp,
        state_root
    ).build_transaction(_tx_params(w3, account, 200000))

    # 4. Podpisywanie i wysyłanie transakcji do sandboxa Anvil, czekamy na potwierdzenie
    tx_receipt = _sign_and_send(w3, transaction, private_key)

    # 5. Asercja QA: status == 1 oznacza sukces; status == 0 oznacza revert (błąd kontraktu)
    assert tx_receipt['status'] == 1, "Transakcja na blockchainie została odrzucona (reverted)!"
    print(f"[QA LOG] Dowód pomyślnie zapisany! Tx Hash: {tx_receipt.transactionHash.hex()}")

    # 6. Weryfikacja stanu: odpytujemy kontrakt czy faktycznie przechowuje nasz root.
    #    To potwierdza, że dane zostały zapisane poprawnie i można je zweryfikować w przyszłości.
    saved_root = contract_instance.functions.registry(mock_timestamp).call()
    assert saved_root == state_root, "Zapisany na blockchainie State Root nie zgadza się z wygenerowanym!"
    print("[QA LOG] Weryfikacja stanu zakończona sukcesem. Integralność danych zabezpieczona.")

def test_zk_aml_compliance_happy_path(web3_setup, contract_instance):
    """
    Scenariusz 2 (Biznesowy): Weryfikacja limitów AML przy użyciu Zero-Knowledge Proof.
    Udowadniamy, że kwota transakcji jest legalna, nie ujawniając jej wartości sieci.
    """
    w3, account, _ = web3_setup
    mock_timestamp = 1717597000
    
    # 1. Proces biznesowy (Off-chain)
    # System finansowy generuje tajny dowód (w tym przypadku symulowany 32-bajtowy poprawny proof)
    mock_zk_proof = b"MATHEMATICAL_ZK_PROOF_VALID_32B" 
    aml_compliant = True  # Publiczne potwierdzenie: kwota < limit
    
    # 2. Transakcja do smart kontraktu
    nonce = w3.eth.get_transaction_count(account.address)
    tx = contract_instance.functions.verifyZKPandPublish(
        mock_timestamp,
        mock_zk_proof,
        aml_compliant
    ).build_transaction({
        'chainId': 31337,
        'gas': 300000,
        'gasPrice': w3.eth.gas_price,
        'nonce': nonce,
    })
    
    signed_tx = w3.eth.account.sign_transaction(tx, private_key=os.getenv("PRIVATE_KEY"))
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    # 3. Asercje QA
    assert tx_receipt['status'] == 1, "Kontrakt odrzucił poprawny dowód ZK!"
    
    # Sprawdzamy czy stan na blockchainie potwierdza pomyślną weryfikację
    is_verified = contract_instance.functions.zkVerificationRegistry(mock_timestamp).call()
    assert is_verified is True
    print(f"\n[ZK QA LOG] Sukces biznesowy: Limit AML zweryfikowany protokołem ZK bez ujawniania kwoty.")


def test_zk_aml_compliance_unhappy_path_invalid_proof(web3_setup, contract_instance):
    """
    Scenariusz 3 (Biznesowy): Próba oszustwa. Sfałszowany lub niepełny dowód ZK 
    musi zostać bezwzględnie odrzucony przez smart kontrakt.
    """
    w3, account, _ = web3_setup
    mock_timestamp = 1717598000
    
    # Symulacja sfałszowanego/uszkodzonego dowodu (zbyt krótka sekwencja bajtów)
    corrupted_zk_proof = b"bad_proof" 
    aml_compliant = True
    
    nonce = w3.eth.get_transaction_count(account.address)
    tx = contract_instance.functions.verifyZKPandPublish(
        mock_timestamp,
        corrupted_zk_proof,
        aml_compliant
    ).build_transaction({
        'chainId': 31337,
        'gas': 300000,
        'gasPrice': w3.eth.gas_price,
        'nonce': nonce,
    })
    
    signed_tx = w3.eth.account.sign_transaction(tx, private_key=os.getenv("PRIVATE_KEY"))
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    # Oczekujemy statusu 0 (Transaction Reverted), ponieważ kontrakt powinien rzucić InvalidZKProof()
    assert tx_receipt['status'] == 0, "BŁĄD QA: Kontrakt zaakceptował sfałszowany dowód ZK!"
    print("[ZK QA LOG] Sukces testu negatywnego: Sfałszowany dowód został prawidłowo zablokowany przez blockchain.")
