"""On-chain GovernanceRegistry client (Phase 0.5 - system admin bootstrap).

Reads the artifact written by `npm run deploy:governance`
(`contracts/deployed/GovernanceRegistry.json`); the address can be overridden
via GOVERNANCE_CONTRACT_ADDRESS. `lockRoot()` is signed by the backend signer
(BACKEND_SIGNER_PRIVATE_KEY), which must be the account that DEPLOYED the
registry - the contract accepts the one-time root lock from nobody else. The
local demo uses Hardhat account #0 for both, exactly like AttendanceLedger.

The lock is permanent for the lifetime of the chain: there is no unlock and
no transfer, so a second bootstrap is rejected on-chain.
"""

import json
from pathlib import Path

from flask import current_app
from web3 import Web3

from .blockchain import BlockchainError

# <repo>/contracts/deployed/GovernanceRegistry.json
# (this file is backend/app/services/governance.py -> parents[3] = repo root)
DEFAULT_ARTIFACT = Path(__file__).resolve().parents[3] / "contracts" / "deployed" / "GovernanceRegistry.json"


def _artifact_path() -> Path:
    configured = current_app.config.get("GOVERNANCE_REGISTRY_ARTIFACT")
    return Path(configured) if configured else DEFAULT_ARTIFACT


def _load_registry():
    """Return ``(w3, contract)`` for GovernanceRegistry or raise BlockchainError."""
    rpc = current_app.config["HARDHAT_RPC_URL"]
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 5}))
    try:
        connected = w3.is_connected()
    except Exception as exc:  # network-level failure (unreachable host, proxy, ...)
        raise BlockchainError(
            "blockchain_unavailable", f"cannot reach the blockchain node at {rpc}: {exc}"
        ) from exc
    if not connected:
        raise BlockchainError(
            "blockchain_unavailable",
            f"blockchain node at {rpc} is not reachable (is `npm run node` running?)",
        )

    artifact_path = _artifact_path()
    address = current_app.config["CONTRACT_ADDRESSES"].get("governance") or ""
    abi = None
    if artifact_path.exists():
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        abi = artifact.get("abi")
        address = address or artifact.get("address", "")
    if not address:
        raise BlockchainError(
            "blockchain_not_configured",
            "GovernanceRegistry address unknown - run `npm run deploy:governance` "
            "or set GOVERNANCE_CONTRACT_ADDRESS",
        )
    if not abi:
        raise BlockchainError(
            "blockchain_not_configured",
            f"GovernanceRegistry ABI not found at {artifact_path}",
        )

    contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)
    return w3, contract


def is_locked() -> bool:
    """Read GovernanceRegistry.isLocked() - True once a root admin is set."""
    _, contract = _load_registry()
    try:
        return bool(contract.functions.isLocked().call())
    except BlockchainError:
        raise
    except Exception as exc:
        raise BlockchainError("blockchain_unavailable", f"isLocked() call failed: {exc}") from exc


def root_admin() -> str:
    """Read GovernanceRegistry.rootAdmin() - the zero address while unlocked."""
    _, contract = _load_registry()
    try:
        return str(contract.functions.rootAdmin().call())
    except BlockchainError:
        raise
    except Exception as exc:
        raise BlockchainError("blockchain_unavailable", f"rootAdmin() call failed: {exc}") from exc


def root_status() -> dict:
    """Both views in one connection: ``{"locked": bool, "root_admin": str}``."""
    _, contract = _load_registry()
    try:
        locked = bool(contract.functions.isLocked().call())
        current_root = str(contract.functions.rootAdmin().call())
    except BlockchainError:
        raise
    except Exception as exc:
        raise BlockchainError(
            "blockchain_unavailable", f"GovernanceRegistry view call failed: {exc}"
        ) from exc
    return {"locked": locked, "root_admin": current_root}


def lock_root(address: str) -> dict:
    """Send GovernanceRegistry.lockRoot(address) from the backend (deployer) signer.

    Returns the transaction hash, block number, block timestamp and the locked
    root address. Raises BlockchainError on any failure, including a
    RootAlreadyLocked revert from a race with the pre-check.
    """
    candidate = str(address or "").strip()
    if not candidate or not Web3.is_address(candidate):
        raise BlockchainError(
            "invalid_root_address",
            f"ADMIN_ROOT_ADDRESS is not a valid Ethereum address: {candidate!r}",
        )
    root = Web3.to_checksum_address(candidate)
    if int(root, 16) == 0:
        raise BlockchainError(
            "invalid_root_address", "ADMIN_ROOT_ADDRESS must not be the zero address"
        )

    w3, contract = _load_registry()
    private_key = current_app.config["BACKEND_SIGNER_PRIVATE_KEY"]
    signer = w3.eth.account.from_key(private_key)

    try:
        gas = contract.functions.lockRoot(root).estimate_gas({"from": signer.address})
        transaction = contract.functions.lockRoot(root).build_transaction(
            {
                "from": signer.address,
                "nonce": w3.eth.get_transaction_count(signer.address),
                "chainId": current_app.config["CHAIN_ID"],
                "gas": int(gas * 1.3) + 21_000,
                "gasPrice": w3.eth.gas_price,
            }
        )
        signed = w3.eth.account.sign_transaction(transaction, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    except BlockchainError:
        raise
    except Exception as exc:
        text = f"{exc} {getattr(exc, 'name', '')}"
        if "RootAlreadyLocked" in text:
            raise BlockchainError(
                "root_already_locked",
                "the system root address is already locked on-chain",
            ) from exc
        if "NotDeployer" in text:
            raise BlockchainError(
                "not_deployer",
                "lockRoot() may only be sent by the account that deployed "
                "GovernanceRegistry - BACKEND_SIGNER_PRIVATE_KEY is a different account",
            ) from exc
        if "ZeroRootAddress" in text:
            raise BlockchainError(
                "invalid_root_address", "ADMIN_ROOT_ADDRESS must not be the zero address"
            ) from exc
        raise BlockchainError("blockchain_error", f"lockRoot() transaction failed: {exc}") from exc

    if receipt.status != 1:
        raise BlockchainError(
            "blockchain_error",
            "lockRoot() transaction reverted on-chain (already locked, or the "
            "backend signer is not the registry deployer)",
        )

    block = w3.eth.get_block(receipt.blockNumber)
    return {
        # hexbytes >= 2.0 drops the 0x prefix from .hex(); keep the canonical form.
        "transaction_hash": Web3.to_hex(receipt.transactionHash),
        "block_number": receipt.blockNumber,
        "locked_at": int(block.timestamp),
        "root_admin": root,
        "signer": signer.address,
    }
