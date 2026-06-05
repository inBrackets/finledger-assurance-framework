// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/AttestationRegistry.sol";

contract AttestationRegistryTest extends Test {
    AttestationRegistry public registry;
    address public admin = address(1);
    address public hacker = address(2);

    function setUp() public {
        // Uruchamiamy kontrakt jako 'admin'
        vm.prank(admin);
        registry = new AttestationRegistry();
    }

    // 1. Happy Path: Test sprawdzający poprawną publikację
    function test_PublishAttestation() public {
        bytes32 mockRoot = keccak256(abi.encodePacked("ISO_20022_DATA_SAMPLE"));
        uint256 timestamp = block.timestamp;

        vm.prank(admin);
        vm.expectEmit(true, true, false, true);
        emit AttestationRegistry.AttestationPublished(timestamp, mockRoot);

        registry.publishAttestation(timestamp, mockRoot);
        assertEq(registry.registry(timestamp), mockRoot);
    }

    // 2. Unhappy Path + FUZZ TESTING: Ktoś inny niż właściciel próbuje wysłać losowy hash
    // Forge automatycznie podstawi pod zmienne 'randomTimestamp' i 'randomRoot' tysiące losowych kombinacji!
    function testFuzz_RevertWhen_NotOwner(uint256 randomTimestamp, bytes32 randomRoot) public {
        // Zapewniamy, że fuzzer nie wygeneruje pustego roota (bo to wywoła inny błąd)
        vm.assume(randomRoot != bytes32(0));

        // Podszywamy się pod hakera
        vm.prank(hacker);
        
        // Oczekujemy, że kontrakt odrzuci transakcję z błędem OnlyOwnerAllowed
        vm.expectRevert(AttestationRegistry.OnlyOwnerAllowed.selector);
        registry.publishAttestation(randomTimestamp, randomRoot);
    }
}