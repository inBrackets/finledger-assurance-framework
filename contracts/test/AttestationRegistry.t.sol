// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/AttestationRegistry.sol";

// 'vm' to obiekt "cheatcode" dostarczany przez forge-std/Test.sol (odziedziczony przez kontrakt Test).
// Cheatcodes to specjalne funkcje dostępne WYŁĄCZNIE w środowisku testowym Forge — nie istnieją
// w prawdziwym EVM na mainnecie. Pozwalają testowi manipulować stanem blockchainu w sposób,
// który normalnie byłby niemożliwy: zmieniać msg.sender, cofać czas, ustawiać salda kont itp.
// Pełna lista cheatcodes: https://book.getfoundry.sh/cheatcodes/
contract AttestationRegistryTest is Test {
    // 'registry' to zmienna stanu przechowująca referencję do wdrożonego kontraktu AttestationRegistry.
    // W Solidity zmienna typu kontraktowego (np. AttestationRegistry) przechowuje pod spodem
    // adres (address) tego kontraktu w sieci EVM oraz skompilowane ABI (listę dostępnych funkcji).
    // Razem pozwalają wywoływać jego funkcje składnią registry.nazwaFunkcji(...).
    // Zadeklarowana na poziomie kontraktu testowego (nie wewnątrz funkcji), bo musi być
    // współdzielona między setUp() a wszystkimi funkcjami testowymi.
    AttestationRegistry public registry;
    address public admin = address(1);
    address public hacker = address(2);

    function setUp() public {
        // vm.prank(address) — cheatcode zmieniający msg.sender dla DOKŁADNIE JEDNEGO następnego wywołania.
        // Po tym jednym wywołaniu msg.sender wraca automatycznie do adresu kontraktu testowego.
        // Tutaj: sprawia, że konstruktor AttestationRegistry() "widzi" msg.sender = admin,
        // więc ustawia owner = admin. Bez vm.prank owner byłby ustawiony na adres tego kontraktu testowego.
        // Jeśli potrzebujesz zmienić sender dla wielu wywołań z rzędu, użyj vm.startPrank() / vm.stopPrank().
        vm.prank(admin);
        // new AttestationRegistry() — wdraża świeży egzemplarz kontraktu do lokalnej sieci EVM
        // zarządzanej przez Forge. Nie wymaga prawdziwego węzła ani Anvila — Forge symuluje całą
        // sieć in-memory. Zwraca obiekt AttestationRegistry z adresem nowo wdrożonego kontraktu.
        // setUp() jest wywoływana przed KAŻDYM testem, więc każdy test dostaje czysty kontrakt
        // bez żadnego wcześniejszego stanu (storage jest zerowane przy każdym wdrożeniu).
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

        // Podszywamy się pod admina — tylko on może wołać publishAttestation (modifier onlyOwner)
        vm.prank(admin);
        // Deklarujemy oczekiwane zdarzenie PRZED wywołaniem funkcji.
        // Parametry vm.expectEmit: weryfikuj topic1 (indexed timestamp),
        // topic2 (indexed stateRoot), pomiń topic3 (brak), sprawdź dane (brak).
        vm.expectEmit(true, true, false, true);
        emit AttestationRegistry.AttestationPublished(timestamp, mockRoot);

        // registry.publishAttestation(timestamp, mockRoot) — wywołuje funkcję na wdrożonym kontrakcie.
        // W przeciwieństwie do web3.py, Forge nie wymaga ręcznego budowania transakcji ani podpisywania kluczem —
        // automatycznie generuje transakcję EVM w lokalnej sieci i wykonuje ją synchronicznie w tym samym bloku.
        // msg.sender dla tego wywołania to adres ustawiony przez vm.prank(admin) powyżej.
        registry.publishAttestation(timestamp, mockRoot);

        // Niezależna weryfikacja stanu: odczytujemy rejestr i porównujemy z oczekiwaną wartością.
        assertEq(registry.registry(timestamp), mockRoot);
    }

    // TYP: Unhappy Path
    // Scenariusz 2: Próba zapisu pustego (zerowego) hash jako State Root.
    // bytes32(0) jest niedozwolony — mógłby zostać błędnie zinterpretowany jako
    // "brak atestacji" przy odczycie z rejestru, co fałszowałoby wynik weryfikacji.
    function test_RevertWhen_EmptyRoot() public {
        // Podszywamy się pod admina — chcemy przetestować błąd InvalidRoot, nie OnlyOwnerAllowed
        vm.prank(admin);

        // vm.expectRevert(selector) — cheatcode informujący Forge, że NASTĘPNE wywołanie MUSI wykonać revert.
        // Jeśli wywołanie nie odrzuci transakcji, test FAILUJE. Jeśli odrzuci z innym błędem, też FAILUJE.
        // Musi stać bezpośrednio przed wywołaniem — nie może być oddzielony innymi instrukcjami.
        //
        // Argument: AttestationRegistry.InvalidRoot.selector
        //   `.selector` to wbudowany mechanizm Solidity zwracający 4-bajtowy identyfikator ABI błędu:
        //   bytes4(keccak256("InvalidRoot()")) — pierwsze 4 bajty hasha sygnatury błędu.
        //   Dzięki temu weryfikujemy że kontrakt rzucił KONKRETNY błąd, a nie jakikolwiek revert
        //   (np. panic, require bez wiadomości lub inny custom error).
        vm.expectRevert(AttestationRegistry.InvalidRoot.selector);
        // To wywołanie CELOWO rzuci revert — vm.expectRevert powyżej "przechwytuje" go jako sukces testu.
        // Gdyby kontrakt nie odrzucił tej transakcji, Forge uznałby test za nieudany.
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

        // 1. Pierwszy zapis atestacji dla danego timestampu.
        //    vm.prank wygasa po jednym wywołaniu, więc przed każdym kolejnym trzeba go powtórzyć.
        vm.prank(admin);
        // Wywołanie zapisuje firstRoot do storage kontraktu pod kluczem 'timestamp'.
        registry.publishAttestation(timestamp, firstRoot);

        // 2. Drugi zapis pod tym samym timestampem — nadpisuje poprzedni bez błędu.
        //    Ponowny vm.prank, bo poprzedni już "zużył się" na wywołaniu z kroku 1.
        vm.prank(admin);
        // To samo wywołanie, ten sam klucz — sprawdzamy czy storage zostanie nadpisany.
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

        // Podszywamy się pod hackera — vm.prank zmienia msg.sender na jego adres dla jednego wywołania.
        // Kontrakt powinien odrzucić żądanie, bo hacker != owner.
        vm.prank(hacker);
        // Weryfikujemy że modifier onlyOwner rzucił błąd OnlyOwnerAllowed(), a nie np. InvalidRoot.
        // .selector = bytes4(keccak256("OnlyOwnerAllowed()")) — 4-bajtowy identyfikator tego błędu.
        vm.expectRevert(AttestationRegistry.OnlyOwnerAllowed.selector);
        // Forge podstawia tu tysiące różnych wartości (randomTimestamp, randomRoot) automatycznie —
        // każda iteracja to osobna transakcja na świeżym stanie kontraktu ze setUp().
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

        // Podszywamy się pod admina — tylko właściciel może wywoływać verifyZKPandPublish
        vm.prank(admin);
        // Weryfikujemy zdarzenie: topic1 (indexed timestamp), reszta bez weryfikacji
        vm.expectEmit(true, false, false, true);
        emit AttestationRegistry.ZKProofVerified(timestamp, true);

        // registry.verifyZKPandPublish(timestamp, validProof, true) — wywołuje drugą publiczną funkcję kontraktu.
        // Parametry: klucz timestampu, bajty dowodu kryptograficznego, publiczne wyjście AML (bool).
        // Forge przekazuje 'validProof' jako calldata — tablica bajtów jest kodowana zgodnie ze standardem ABI.
        // Wywołanie wykonuje się synchronicznie: jeśli rzuci revert, test failuje w tym miejscu.
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

        // Podszywamy się pod admina — chcemy przetestować błąd InvalidZKProof, nie OnlyOwnerAllowed
        vm.prank(admin);
        // Weryfikujemy konkretny błąd walidacji długości dowodu, nie ogólny revert.
        vm.expectRevert(AttestationRegistry.InvalidZKProof.selector);
        // Wywołanie z dowodem o długości 9 — kontrakt sprawdza proof.length < 32 i rzuca revert.
        registry.verifyZKPandPublish(block.timestamp, shortProof, true);
    }

    // TYP: Unhappy Path
    // Scenariusz 7: Transakcja niezgodna z limitami AML (isCompliant = false).
    // Nawet jeśli dowód jest technicznie poprawnej długości, kontrakt musi odmówić
    // zapisu — publiczne wyjście ZK wprost wskazuje na naruszenie limitu AML.
    function test_RevertWhen_ZKProof_NotCompliant() public {
        // Poprawna długość dowodu, ale niezgodność z AML — oba warunki niezależnie powodują revert
        bytes memory validLengthProof = new bytes(32);

        // Podszywamy się pod admina — chcemy przetestować błąd InvalidZKProof z powodu isCompliant=false
        vm.prank(admin);
        // Ten sam selektor co w scenariuszu 6 — kontrakt rzuca ten sam błąd dla obu warunków (|| w if).
        vm.expectRevert(AttestationRegistry.InvalidZKProof.selector);
        // Wywołanie z isCompliant=false — kontrakt sprawdza !isCompliant i rzuca revert,
        // mimo że długość dowodu jest poprawna (32 bajty). Warunek || w if powoduje revert przy spełnieniu choćby jednego.
        registry.verifyZKPandPublish(block.timestamp, validLengthProof, false);
    }

    // TYP: Unhappy Path
    // Scenariusz 8: Nieuprawniony adres próbuje zweryfikować dowód ZK.
    // Zapis wyniku weryfikacji do rejestru on-chain to operacja uprzywilejowana —
    // dostęp musi być ograniczony wyłącznie do właściciela kontraktu.
    function test_RevertWhen_VerifyZKP_NotOwner() public {
        bytes memory validProof = new bytes(32);

        // Podszywamy się pod hackera — msg.sender != owner, kontrakt musi natychmiast odrzucić
        vm.prank(hacker);
        // modifier onlyOwner jest sprawdzany jako pierwszy, zanim kontrakt dotrze do logiki ZK.
        // Weryfikujemy że to właśnie OnlyOwnerAllowed, a nie InvalidZKProof (kolejność ma znaczenie).
        vm.expectRevert(AttestationRegistry.OnlyOwnerAllowed.selector);
        // Wywołanie z msg.sender = hacker — modifier onlyOwner odpali revert zanim
        // kontrakt w ogóle dotrze do sprawdzenia długości dowodu.
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

        // Podszywamy się pod admina — chcemy przetestować odrzucenie krótkiego dowodu, nie brak uprawnień
        vm.prank(admin);
        // Forge weryfikuje selektor przy każdej z tysięcy iteracji fuzzera — żadna nie może "przeciec".
        vm.expectRevert(AttestationRegistry.InvalidZKProof.selector);
        // Forge wywołuje tę linię dla każdej iteracji fuzzera z inną wartością shortLength (0–31).
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

        // Podszywamy się pod admina — vm.prank musi być powtórzony w każdej iteracji fuzzera
        vm.prank(admin);
        // Wywołanie z dowodem o długości validLength (32–255) — każda iteracja fuzzera
        // używa innej długości, ale wszystkie muszą przejść bez revertu.
        registry.verifyZKPandPublish(timestamp, validProof, true);

        assertTrue(registry.zkVerificationRegistry(timestamp));
    }
}
