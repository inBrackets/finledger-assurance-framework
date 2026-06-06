// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/AttestationRegistry.sol";

contract AttestationRegistryTest is Test {
    AttestationRegistry public registry;
    address public admin = address(1);
    address public hacker = address(2);

    function setUp() public {
        // Uruchamiamy świeżą instancję kontraktu przed KAŻDYM testem (nie raz na moduł).
        // vm.prank sprawia, że następne wywołanie jest wykonane jako 'admin',
        // więc konstruktor ustawi owner = admin.
        vm.prank(admin);
        registry = new AttestationRegistry();
    }

    // =========================================================================
    // publishAttestation — testy funkcjonalne
    // =========================================================================

    // TYP: Happy Path
    // Scenariusz 1: Poprawna publikacja atestacji przez właściciela kontraktu.
    // Weryfikujemy zarówno zapis stanu (storage) jak i emisję zdarzenia — to dwa
    // niezależne kanały informacji i oba muszą być poprawne jednocześnie.
    function test_PublishAttestation() public {
        bytes32 mockRoot = keccak256(abi.encodePacked("ISO_20022_DATA_SAMPLE"));
        uint256 timestamp = block.timestamp;

        vm.prank(admin);
        // Deklarujemy oczekiwane zdarzenie PRZED wywołaniem funkcji.
        // Parametry vm.expectEmit: weryfikuj topic1 (indexed timestamp),
        // topic2 (indexed stateRoot), pomiń topic3 (brak), sprawdź dane (brak).
        vm.expectEmit(true, true, false, true);
        emit AttestationRegistry.AttestationPublished(timestamp, mockRoot);

        registry.publishAttestation(timestamp, mockRoot);

        // Niezależna weryfikacja stanu: odczytujemy rejestr i porównujemy z oczekiwaną wartością.
        assertEq(registry.registry(timestamp), mockRoot);
    }

    // TYP: Unhappy Path
    // Scenariusz 2: Próba zapisu pustego (zerowego) hash jako State Root.
    // bytes32(0) jest niedozwolony — mógłby zostać błędnie zinterpretowany jako
    // "brak atestacji" przy odczycie z rejestru, co fałszowałoby wynik weryfikacji.
    function test_RevertWhen_EmptyRoot() public {
        vm.prank(admin);
        // Oczekujemy błędu InvalidRoot — kontrakt musi jawnie odrzucić puste dane wejściowe.
        vm.expectRevert(AttestationRegistry.InvalidRoot.selector);
        registry.publishAttestation(block.timestamp, bytes32(0));
    }

    // TYP: Edge Case (Kolizja timestampów — dokumentacja zachowania)
    // Scenariusz 3: Drugi zapis pod tym samym kluczem (timestamp) nadpisuje pierwszy.
    // Kontrakt nie blokuje nadpisywania — ten test dokumentuje tę decyzję projektową
    // i służy jako sygnał ostrzegawczy: jeśli wymaganie się zmieni (immutable attestations),
    // ten test natychmiast się posypie i wymusi aktualizację kontraktu.
    function test_PublishAttestation_OverwritesSameTimestamp() public {
        uint256 timestamp = block.timestamp;
        bytes32 firstRoot  = keccak256(abi.encodePacked("BATCH_001"));
        bytes32 secondRoot = keccak256(abi.encodePacked("BATCH_002"));

        // 1. Pierwszy zapis atestacji dla danego timestampu
        vm.prank(admin);
        registry.publishAttestation(timestamp, firstRoot);

        // 2. Drugi zapis pod tym samym timestampem — nadpisuje poprzedni bez błędu
        vm.prank(admin);
        registry.publishAttestation(timestamp, secondRoot);

        // 3. Rejestr przechowuje wyłącznie najnowszą wartość — pierwsza jest bezpowrotnie utracona.
        //    To może być błąd projektowy wymagający decyzji architektonicznej.
        assertEq(registry.registry(timestamp), secondRoot);
    }

    // TYP: Unhappy Path + Fuzz Testing
    // Scenariusz 4: Nieuprawniony adres próbuje opublikować atestację z losowymi danymi.
    // Forge automatycznie podstawia tysiące kombinacji (randomTimestamp, randomRoot),
    // gwarantując że kontrola dostępu działa dla DOWOLNYCH danych, nie tylko jednego przypadku.
    function testFuzz_RevertWhen_PublishAttestation_NotOwner(
        uint256 randomTimestamp,
        bytes32 randomRoot
    ) public {
        // Odrzucamy pusty root — wywołałby InvalidRoot zamiast OnlyOwnerAllowed,
        // co zaburzyłoby cel tego konkretnego testu.
        vm.assume(randomRoot != bytes32(0));

        vm.prank(hacker);
        vm.expectRevert(AttestationRegistry.OnlyOwnerAllowed.selector);
        registry.publishAttestation(randomTimestamp, randomRoot);
    }

    // =========================================================================
    // verifyZKPandPublish — testy funkcjonalne
    // =========================================================================

    // TYP: Happy Path
    // Scenariusz 5: Poprawna weryfikacja dowodu ZK przez właściciela kontraktu.
    // Minimalna długość poprawnego dowodu to 32 bajty (SHA-256 output / granica z kontraktu).
    // Weryfikujemy emisję zdarzenia oraz zapis statusu zgodności w rejestrze.
    function test_VerifyZKP_HappyPath() public {
        uint256 timestamp = block.timestamp;
        // Dokładnie 32 bajty — testujemy granicę (boundary value), nie tylko "pewne" wartości
        bytes memory validProof = new bytes(32);

        vm.prank(admin);
        // Weryfikujemy zdarzenie: topic1 (indexed timestamp), reszta bez weryfikacji
        vm.expectEmit(true, false, false, true);
        emit AttestationRegistry.ZKProofVerified(timestamp, true);

        registry.verifyZKPandPublish(timestamp, validProof, true);

        // Weryfikacja stanu: status zgodności musi zostać zapisany jako 'true'
        assertTrue(registry.zkVerificationRegistry(timestamp));
    }

    // TYP: Unhappy Path
    // Scenariusz 6: Dowód ZK za krótki (poniżej 32 bajtów) musi zostać odrzucony.
    // Dowód o długości 9 bajtów symuluje uszkodzony lub sfałszowany pakiet —
    // analogicznie do 'corrupted_zk_proof = b"bad_proof"' z test_pipeline.py.
    function test_RevertWhen_ZKProof_TooShort() public {
        bytes memory shortProof = new bytes(9);

        vm.prank(admin);
        vm.expectRevert(AttestationRegistry.InvalidZKProof.selector);
        registry.verifyZKPandPublish(block.timestamp, shortProof, true);
    }

    // TYP: Unhappy Path
    // Scenariusz 7: Transakcja niezgodna z limitami AML (isCompliant = false).
    // Nawet jeśli dowód jest technicznie poprawnej długości, kontrakt musi odmówić
    // zapisu — publiczne wyjście ZK wprost wskazuje na naruszenie limitu AML.
    function test_RevertWhen_ZKProof_NotCompliant() public {
        // Poprawna długość dowodu, ale niezgodność z AML — oba warunki niezależnie powodują revert
        bytes memory validLengthProof = new bytes(32);

        vm.prank(admin);
        vm.expectRevert(AttestationRegistry.InvalidZKProof.selector);
        registry.verifyZKPandPublish(block.timestamp, validLengthProof, false);
    }

    // TYP: Unhappy Path
    // Scenariusz 8: Nieuprawniony adres próbuje zweryfikować dowód ZK.
    // Zapis wyniku weryfikacji do rejestru on-chain to operacja uprzywilejowana —
    // dostęp musi być ograniczony wyłącznie do właściciela kontraktu.
    function test_RevertWhen_VerifyZKP_NotOwner() public {
        bytes memory validProof = new bytes(32);

        vm.prank(hacker);
        vm.expectRevert(AttestationRegistry.OnlyOwnerAllowed.selector);
        registry.verifyZKPandPublish(block.timestamp, validProof, true);
    }

    // TYP: Fuzz Testing (granica długości dowodu — strona odrzucenia)
    // Scenariusz 9: Dla KAŻDEJ długości dowodu poniżej 32 bajtów kontrakt MUSI odrzucić.
    // Forge przetestuje wszystkie możliwe wartości uint8 z zakresu 0–31 (256 kombinacji),
    // zabezpieczając przed regresją w logice walidacji np. po zmianie progu w kontrakcie.
    function testFuzz_RevertWhen_ZKProof_AnyShortLength(uint8 shortLength) public {
        // Zawężamy przestrzeń fuzzera do wartości poniżej granicy walidacji
        vm.assume(shortLength < 32);

        bytes memory shortProof = new bytes(shortLength);

        vm.prank(admin);
        vm.expectRevert(AttestationRegistry.InvalidZKProof.selector);
        registry.verifyZKPandPublish(block.timestamp, shortProof, true);
    }

    // TYP: Fuzz Testing (granica długości dowodu — strona akceptacji)
    // Scenariusz 10: Dla DOWOLNEJ długości dowodu >= 32 bajtów kontrakt MUSI zaakceptować.
    // Gwarantuje, że walidacja nie jest przypadkowo zbyt restrykcyjna i nie odrzuca
    // poprawnych kryptograficznie dowodów (np. 64-bajtowych dla Groth16 lub dłuższych).
    function testFuzz_VerifyZKP_AcceptsAnyValidLength(uint8 validLength) public {
        // Zawężamy do 32–255 (maksimum uint8) — cały zakres powyżej granicy walidacji
        vm.assume(validLength >= 32);

        bytes memory validProof = new bytes(validLength);
        uint256 timestamp = block.timestamp;

        vm.prank(admin);
        registry.verifyZKPandPublish(timestamp, validProof, true);

        assertTrue(registry.zkVerificationRegistry(timestamp));
    }
}
