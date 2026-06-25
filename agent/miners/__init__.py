from .peakminer import PeakMinerAdapter
from .srbminer import SRBMinerAdapter

ADAPTERS = {
    "peakminer": PeakMinerAdapter(),
    "srbminer": SRBMinerAdapter(),
}


def get_adapter(miner_name: str):
    adapter = ADAPTERS.get(miner_name)
    if not adapter:
        valid = ", ".join(ADAPTERS.keys())
        raise ValueError(f"Unknown miner '{miner_name}'. Supported: {valid}")
    return adapter
