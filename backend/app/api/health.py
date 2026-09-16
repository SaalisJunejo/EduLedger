"""GET /api/v1/health - service and dependency status."""

from datetime import datetime, timezone

from flask import current_app, jsonify

from .. import __version__
from . import api_v1


@api_v1.get("/health")
def health():
    """Report that the service is up and which dependencies are configured.

    The dependency flags are config checks (fast, no external calls), not
    liveness probes - use the Hardhat node / IPFS daemon logs for that.
    """
    config = current_app.config
    contracts = config["CONTRACT_ADDRESSES"]

    return jsonify(
        {
            "status": "ok",
            "service": "eduledger-backend",
            "version": __version__,
            "environment": config["ENVIRONMENT"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dependencies": {
                "database_configured": bool(config["DB_URL"]),
                "blockchain_rpc_configured": bool(config["HARDHAT_RPC_URL"]),
                "ipfs_configured": bool(config["IPFS_API_URL"]),
                "contracts_configured": {
                    name: bool(address) for name, address in contracts.items()
                },
            },
        }
    )
