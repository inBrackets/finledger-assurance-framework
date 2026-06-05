// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title AttestationRegistry
 * @dev Kontrakt do zakotwiczania dowodów kryptograficznych potoków finansowych.
 */
contract AttestationRegistry {
    address public owner;
    
    // Mapowanie: Timestamp transakcji -> Wygenerowany State Root (Merkle Root)
    mapping(uint256 => bytes32) public registry;

    // Zdarzenie emitowane przy prawidłowej publikacji dowodu
    event AttestationPublished(uint256 indexed timestamp, bytes32 indexed stateRoot);

    error OnlyOwnerAllowed();
    error InvalidRoot();

    modifier onlyOwner() {
        if (msg.sender != owner) revert OnlyOwnerAllowed();
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    /**
     * @notice Publikuje nowy dowód kryptograficzny w blockchainie
     * @param timestamp Czas wygenerowania paczki danych (match key)
     * @param stateRoot Hash Merkle Tree reprezentujący stan danych
     */
    function publishAttestation(uint256 timestamp, bytes32 stateRoot) external onlyOwner {
        if (stateRoot == bytes32(0)) revert InvalidRoot();
        
        registry[timestamp] = stateRoot;
        
        emit AttestationPublished(timestamp, stateRoot);
    }
}