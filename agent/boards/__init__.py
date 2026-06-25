from .octominer import OctominerAdapter
from .none import NoneAdapter

ADAPTERS = {
    "octominer": OctominerAdapter,
    "none": NoneAdapter,
}


def get_board_adapter(board_type: str = None):
    """
    If board_type is given, use that adapter directly. Otherwise auto-detect:
    try each known adapter's is_present() and use the first that matches,
    falling back to NoneAdapter if nothing is found.
    """
    if board_type:
        cls = ADAPTERS.get(board_type)
        if not cls:
            raise ValueError(f"Unknown board type '{board_type}'. Supported: {', '.join(ADAPTERS.keys())}")
        return cls()

    for name, cls in ADAPTERS.items():
        if name == "none":
            continue
        adapter = cls()
        if adapter.is_present():
            return adapter

    return NoneAdapter()
