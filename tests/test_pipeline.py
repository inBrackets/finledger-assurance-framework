import os
import json
import hashlib
import pytest
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

@pytest.fixture(scope="module")
def web3_setup():
    """Inicjalizacja połączenia z Anvilem oraz konfiguracja konta."""
    rpc_url = os.getenv("RPC_URL", "http://127.0.0.1:8545")
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    assert w3.is_connected(), "Brak połączenia z lokalnym sandboxem Anvil!"
    
    # Pobieramy klucz prywatny admina z .env i ustawiamy jego adres
    private_key = os.getenv("PRIVATE_KEY")
    account = w3.eth.account.from_key(private_key)
    
    return w3, account

@pytest.fixture(scope="module")
def contract_instance(web3_setup):
    """Automatycznie wdraża świeżą instancję kontraktu do Anvila przed testem."""
    w3, account = web3_setup
    
    # Ładowanie skompilowanego artefaktu
    artifact_path = os.path.join("contracts", "out", "AttestationRegistry.sol", "AttestationRegistry.json")
    with open(artifact_path, "r") as f:
        artifact = json.load(f)
        
    abi = artifact["abi"]
    bytecode = artifact["bytecode"]["object"]
    
    # Tworzymy obiekt fabryki kontraktu
    ContractFactory = w3.eth.contract(abi=abi, bytecode=bytecode)
    
    # Budujemy transakcję wdrożenia (deploy)
    nonce = w3.eth.get_transaction_count(account.address)
    deploy_tx = ContractFactory.constructor().build_transaction({
        'chainId': 31337,
        'gas': 1000000,
        'gasPrice': w3.eth.gas_price,
        'nonce': nonce,
    })
    
    # Podpisujemy i wysyłamy transakcję wdrożenia
    signed_deploy = w3.eth.account.sign_transaction(deploy_tx, private_key=os.getenv("PRIVATE_KEY"))
    tx_hash = w3.eth.send_raw_transaction(signed_deploy.raw_transaction)
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    print(f"\n[QA LOG] Kontrakt pomyślnie wdrożony pod adres: {tx_receipt.contractAddress}")
    
    # Zwracamy gotową instancję połączoną z nowym, prawidłowym adresem
    return w3.eth.contract(address=tx_receipt.contractAddress, abi=abi)


def test_e2e_blockchain_settlement_pipeline(web3_setup, contract_instance):
    """
    Pełen test E2E: Przetwarzanie danych -> Generowanie dowodu -> Zapis na Blockchainie.
    """
    w3, account = web3_setup
    
    # 1. Pipeline danych (Symulacja transakcji finansowej)
    raw_data = '{"sender": "Bank_A", "receiver": "Bank_B", "amount": 5000000}'
    canonical_data = raw_data.strip().replace(" ", "")
    
    # 2. Generowanie dowodu (State Root) - 32 bajty
    state_root = hashlib.sha256(canonical_data.encode('utf-8')).digest()
    mock_timestamp = 1717596000  # Przykładowy timestamp (Match Key)

    print(f"\n[QA LOG] Wygenerowany dowód danych: {state_root.hex()}")

    # 3. Budowanie transakcji blockchainowej przez Pythona do naszego kontraktu
    nonce = w3.eth.get_transaction_count(account.address)
    
    # Wywołujemy funkcję 'publishAttestation' z naszego kontraktu w Solidity
    transaction = contract_instance.functions.publishAttestation(
        mock_timestamp, 
        state_root
    ).build_transaction({
        'chainId': 31337,  # Domyślny ID sieci dla Anvila
        'gas': 200000,
        'gasPrice': w3.eth.gas_price,
        'nonce': nonce,
    })

    # 4. Podpisywanie i wysyłanie transakcji do sandboxa Anvil
    signed_tx = w3.eth.account.sign_transaction(transaction, private_key=os.getenv("PRIVATE_KEY"))
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    
    # Czekamy na potwierdzenie transakcji (w Anvilu dzieje się to natychmiast)
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    # 5. Asercja QA: Czy transakcja zakończyła się sukcesem (status == 1)?
    assert tx_receipt['status'] == 1, "Transakcja na blockchainie została odrzucona (reverted)!"
    print(f"[QA LOG] Dowód pomyślnie zapisany! Tx Hash: {tx_hash.hex()}")

    # 6. Weryfikacja stanu: Odpytujemy kontrakt czy faktycznie przechowuje nasz root
    saved_root = contract_instance.functions.registry(mock_timestamp).call()
    assert saved_root == state_root, "Zapisany na blockchainie State Root nie zgadza się z wygenerowanym!"
    print("[QA LOG] Weryfikacja stanu zakończona sukcesem. Integralność danych zabezpieczona.")