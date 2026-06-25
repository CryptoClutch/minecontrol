from .base import BoardAdapter


class NoneAdapter(BoardAdapter):
    """For rigs with no dedicated board fan controller (e.g. plain Linux
    boxes like superserver) - board-level fan profiles simply don't apply
    here, since fan control for these rigs goes through the per-GPU
    OCProfile -> nvidia-settings path instead."""
    name = "none"

    def is_present(self) -> bool:
        return True  # always "present" in the sense that it's a valid no-op choice

    def apply_fan_config(self, board_fan_cfg: dict) -> None:
        pass

    def read_board_telemetry(self) -> dict:
        return {}
