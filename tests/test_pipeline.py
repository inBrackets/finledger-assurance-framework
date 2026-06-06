import os
import json
import hashlib
import pytest
from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import TimeExhausted

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
    w3, account, private_key = web3_setup
    mock_timestamp = 1717597000

    # 1. Proces biznesowy (Off-chain)
    # System finansowy generuje tajny dowód (w tym przypadku symulowany 32-bajtowy poprawny proof)
    mock_zk_proof = b"MATHEMATICAL_ZK_PROOF_VALID_32B_"
    aml_compliant = True  # Publiczne potwierdzenie: kwota < limit

    # 2. Budowanie, podpisanie i wysłanie transakcji do smart kontraktu
    tx = contract_instance.functions.verifyZKPandPublish(
        mock_timestamp,
        mock_zk_proof,
        aml_compliant
    ).build_transaction(_tx_params(w3, account, 300000))
    tx_receipt = _sign_and_send(w3, tx, private_key)

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
    w3, account, private_key = web3_setup
    mock_timestamp = 1717598000

    # Symulacja sfałszowanego/uszkodzonego dowodu (zbyt krótka sekwencja bajtów)
    corrupted_zk_proof = b"bad_proof"
    aml_compliant = True

    # Budowanie, podpisanie i wysłanie transakcji do smart kontraktu
    tx = contract_instance.functions.verifyZKPandPublish(
        mock_timestamp,
        corrupted_zk_proof,
        aml_compliant
    ).build_transaction(_tx_params(w3, account, 300000))
    tx_receipt = _sign_and_send(w3, tx, private_key)

    # Oczekujemy statusu 0 (Transaction Reverted), ponieważ kontrakt powinien rzucić InvalidZKProof()
    assert tx_receipt['status'] == 0, "BŁĄD QA: Kontrakt zaakceptował sfałszowany dowód ZK!"
    print("[ZK QA LOG] Sukces testu negatywnego: Sfałszowany dowód został prawidłowo zablokowany przez blockchain.")

def test_unhappy_path_gas_spike_and_revert(web3_setup, contract_instance):
    """
    Scenariusz 4 (Negatywny): Obsługa skoków gazu i niedoszacowanych transakcji.
    Weryfikuje, czy framework prawidłowo wychwytuje i obsługuje awarie sieciowe,
    gdy warunki na blockchainie ulegają nagłemu pogorszeniu.
    """
    w3, account, private_key = web3_setup
    mock_timestamp = 1717599000
    mock_zk_proof = b"MATHEMATICAL_ZK_PROOF_VALID_32B_"
    aml_compliant = True

    # Pobieramy aktualną liczbę transakcji (nonce) dla portfela
    nonce = w3.eth.get_transaction_count(account.address)

    # =========================================================================
    # 1. SYMULACJA SKOKU GAZU (Niedoszacowana cena gazu - Gas Price Too Low)
    # =========================================================================
    # Celowo ustawiamy gasPrice na drastycznie niską wartość (1 wei), 
    # która na pewno jest poniżej minimalnej ceny (base fee) sieci Anvil.
    low_gas_price = 1 
    
    tx_underpriced = contract_instance.functions.verifyZKPandPublish(
        mock_timestamp,
        mock_zk_proof,
        aml_compliant
    ).build_transaction({
        'chainId': 31337,
        'gas': 300000,
        'gasPrice': low_gas_price,
        'nonce': nonce,
    })

    # Podpisujemy wadliwą transakcję
    signed_tx = w3.eth.account.sign_transaction(tx_underpriced, private_key=private_key)

    print("\n[QA LOG] Wstrzykiwanie niedoszacowanej transakcji (symulacja nagłego skoku cen gazu)...")
    
    # Oczekujemy, że węzeł blockchain lub biblioteka web3 natychmiast odrzuci tę transakcję
    with pytest.raises(Exception) as exc_info:
        w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    
    # Sprawdzamy, czy infrastruktura poprawnie przechwyciła błąd EVM o zbyt niskiej opłacie
    assert "max fee per gas less than block base fee" in str(exc_info.value).lower() or "underpriced" in str(exc_info.value).lower()
    print(f"[QA LOG] Sukces: Infrastruktura bezpiecznie odrzuciła transakcję. Powód: {exc_info.value}")


    # =========================================================================
    # 2. SYMULACJA WYCZERPANIA LIMITU GAZU (Out of Gas / Intrinsic Gas Too Low)
    # =========================================================================
    # Pobieramy aktualną, poprawną rynkową cenę gazu, aby ominąć poprzedni błąd
    market_gas_price = w3.eth.gas_price
    
    # Celowo ustawiamy limit gazu na śmiesznie niski poziom (21000). 
    # Tyle wystarczy na zwykły przelew ETH, ale to za mało na wykonanie logiki smart kontraktu.
    insufficient_gas_limit = 21000 

    tx_out_of_gas = contract_instance.functions.verifyZKPandPublish(
        mock_timestamp,
        mock_zk_proof,
        aml_compliant
    ).build_transaction({
        'chainId': 31337,
        'gas': insufficient_gas_limit,
        'gasPrice': market_gas_price,
        'nonce': nonce, # Ponownie używamy tego samego nonce, bo poprzednia transakcja nigdy nie trafiła do bloku
    })

    signed_out_of_gas_tx = w3.eth.account.sign_transaction(tx_out_of_gas, private_key=private_key)

    print("[QA LOG] Wstrzykiwanie transakcji ze zbyt niskim limitem gazu (wymuszenie błędu Out-of-Gas)...")

    # W zależności od konfiguracji węzła, transakcja może zostać odrzucona przy wysyłce 
    # LUB wejść do bloku i zakończyć się statusem porażki (0). Test obsługuje oba przypadki.
    try:
            tx_hash = w3.eth.send_raw_transaction(signed_out_of_gas_tx.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=5)
            
            # Jeśli transakcja została wykopana w bloku, jej status musi wynosić 0 (Revert)
            assert receipt['status'] == 0, "Transakcja powinna zakończyć się błędem z powodu braku gazu!"
            print("[QA LOG] Sukces: Transakcja została odrzucona na poziomie EVM (Status: 0).")
            
    except TimeExhausted as te:
        # Anvil przyjął transakcję do mempoola, ale z braku gazu nigdy jej nie wykopał (wygasła po 5s)
        print(f"[QA LOG] Sukces: Transakcja prawidłowo utknęła i wygasła w mempoolu z powodu zbyt niskiego limitu gazu.")
        assert "not in the chain" in str(te).lower() or "timeout" in str(te).lower()

    except Exception as e:
        # Jeśli węzeł zablokował ją natychmiast jeszcze przed dodaniem do kolejki mempool
        assert "intrinsic gas too low" in str(e).lower() or "gas limit" in str(e).lower() or "out of gas" in str(e).lower()
        print(f"[QA LOG] Sukces: Silnik sieci zablokował transakcję przed rozgłoszeniem. Powód: {e}")


def test_mempool_gas_priority_ordering(web3_setup):
    """
    Test dowodzący, że walidatorzy (węzeł Anvil) traktują priorytetowo 
    transakcje z wyższym Gas Price podczas wybierania ich z mempoolu do bloku.
    """
    w3, account_1, private_key_1 = web3_setup
    
    # Do tego testu potrzebujemy drugiego konta, aby wysłać transakcje niezależnie
    # Anvil domyślnie udostępnia 10 kont. Pobieramy drugie konto (indeks 1).
    account_2_address = w3.eth.accounts[1]
    # W prawdziwym setupie pobrałbyś też klucz prywatny konta 2, 
    # ale na potrzeby testu wyślemy transakcję 2 bezpośrednio przez węzeł lub podpiszemy.
    
    # Zapamiętujemy oryginalny limit bloku, aby przywrócić go po teście
    original_gas_limit = w3.eth.get_block('latest')['gasLimit']

    # 1. ZATRZYMUJEMY AUTOMATYCZNE KOPANIE BLOKÓW (Wyłączamy Auto-mine w Anvilu)
    # Od teraz transakcje będą trafiać do mempoolu i tam "wisieć".
    w3.provider.make_request("evm_setAutomine", [False])
    # Ograniczamy pojemność bloku do 30 000 gazu — wystarczy na jedną transakcję ETH (21 000),
    # ale za mało na dwie (42 000). Wymusza to selekcję priorytetową z mempoolu.
    w3.provider.make_request("evm_setBlockGasLimit", [hex(30000)])

    try:
        # Pobieramy aktualne parametry sieci
        base_fee = w3.eth.get_block('latest')['baseFeePerGas']
        nonce_1 = w3.eth.get_transaction_count(account_1.address)
        nonce_2 = w3.eth.get_transaction_count(account_2_address)
        
        # Przygotowujemy zwykły przelew testowy (najprostsza transakcja)
        tx_template = {
            'type': '0x2',  # Transakcja EIP-1559 (nowy standard Ethereum)
            'chainId': 31337,
            'gas': 21000,
            'value': w3.to_wei(0.1, 'ether'),
        }

        # T1: Transakcja z NISKIM priorytetem (Low Fee)
        tx_low_fee = {
            **tx_template,
            'nonce': nonce_1,
            'to': w3.eth.accounts[3],
            'maxFeePerGas': base_fee + w3.to_wei(1, 'gwei'),        # Łączna stawka
            'maxPriorityFeePerGas': w3.to_wei(1, 'gwei')            # Napiwek dla górnika
        }
        
        # T2: Transakcja z WYSOKIM priorytetem (High Fee - 50x większy napiwek!)
        tx_high_fee = {
            **tx_template,
            'nonce': nonce_2,
            'to': w3.eth.accounts[4],
            'maxFeePerGas': base_fee + w3.to_wei(50, 'gwei'),
            'maxPriorityFeePerGas': w3.to_wei(50, 'gwei')
        }

        # Podpisujemy obie transakcje
        # (Dla uproszczenia zakładamy, że podpisujemy kluczem konta pierwszego, 
        # w realnym teście upewnij się, że masz klucze do obu kont lub użyj w3.eth.send_transaction)
        signed_low = w3.eth.account.sign_transaction(tx_low_fee, private_key=private_key_1)
        
        # Aby wysłać z konta 2 bez posiadania jawnego klucza w zmiennej, 
        # możemy poprosić węzeł Anvil o jej podpisanie i wysłanie (Anvil ma odblokowane konta):
        w3.provider.make_request("anvil_impersonateAccount", [account_2_address])
        
        # 2. WSTRZYKUJEMY OBIE TRANSAKCJE DO MEMPOOLU
        print("\n[QA LOG] Wysyłanie transakcji z niską opłatą do mempoolu...")
        tx_hash_low = w3.eth.send_raw_transaction(signed_low.raw_transaction)
        
        print("[QA LOG] Wysyłanie transakcji z wysoką opłatą do mempoolu...")
        tx_hash_high = w3.eth.send_transaction({
            'from': account_2_address,
            'to': w3.eth.accounts[4],
            'value': w3.to_wei(0.1, 'ether'),
            'gas': 21000,
            'maxFeePerGas': base_fee + w3.to_wei(50, 'gwei'),
            'maxPriorityFeePerGas': w3.to_wei(50, 'gwei'),
            'nonce': nonce_2
        })

        # W tym momencie OBIE transakcje wiszą w mempoolu. Żadna nie jest wykopana.
        
        # 3. WYMUSZAMY RĘCZNE WYKOPANIE TYLKO JEDNEGO BLOKU
        print("[QA LOG] Nakazywanie Anvilowi wykopania dokładnie 1 nowego bloku...")
        w3.provider.make_request("evm_mine", [])

        # 4. WERYFIKACJA (ASERCJA)
        # Pobieramy najnowszy wykopany blok i sprawdzamy, które transakcje do niego weszły
        latest_block = w3.eth.get_block('latest', full_transactions=True)
        block_tx_hashes = [tx['hash'] for tx in latest_block['transactions']]

        print(f"[QA LOG] Transakcje, które znalazły się w bloku: { [h.hex() for h in block_tx_hashes] }")
        
        # Dowód: Transakcja High Fee MUSI być w bloku, a Low Fee MUSI pozostać w mempoolu.
        # Weryfikacja obu stron jest konieczna — bez drugiej asercji test przechodzi trywialnie,
        # gdy Anvil wkopuje wszystkie oczekujące transakcje jednocześnie.
        assert tx_hash_high in block_tx_hashes, "BŁĄD: Transakcja z wysoką opłatą powinna zostać wykopana w pierwszej kolejności!"
        assert tx_hash_low not in block_tx_hashes, "BŁĄD: Transakcja z niską opłatą nie powinna trafić do bloku, gdy limit gazu jest wyczerpany!"
        print("[QA LOG] SUKCES: Transakcja z wyższym Gas Fee opuściła mempool jako pierwsza!")

    finally:
        # KLUCZOWE: Zawsze przywracamy auto-mine i limit bloku na koniec testu,
        # aby nie popsuć działania innych testów w całym pipeline!
        w3.provider.make_request("evm_setBlockGasLimit", [hex(original_gas_limit)])
        w3.provider.make_request("evm_setAutomine", [True])
        w3.provider.make_request("anvil_stopImpersonatingAccount", [account_2_address])