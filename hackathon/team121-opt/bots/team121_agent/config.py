"""Search limits in turns or milliseconds. No third-party dependencies."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    budget_ms: int = 95
    referee_ms: int = 200
    enum_ms: int = 28
    sim_reserve_ms: int = 35
    sim_call_ms: int = 12
    max_turns: int = 600
    max_macro: int = 32
    max_leg: int = 12
    shortlist: int = 20
    enemy_horizon: int = 34
    max_cut: int = 4
    use_sim: bool = True
    allow_fourth_leg: bool = True
    allow_intercept: bool = True
    relocate: bool = True
