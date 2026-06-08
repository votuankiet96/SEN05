"""Symbol matching between SEN05 symbols and account-scoped cTrader symbols."""

from __future__ import annotations

import re

from .models import RemoteSymbol, SymbolMatch, TargetSymbol

NORMALIZE_RE = re.compile(r"[^A-Z0-9]+")

LOCAL_ALIASES: dict[str, tuple[str, ...]] = {
    "FR40": ("FR40", "FRA40", "CAC40", "FRANCE40"),
    "DE40": ("DE40", "GER40", "DAX40", "DAX", "GERMANY40"),
    "HK50": ("HK50", "HSI", "HONGKONG50"),
    "J225": ("J225", "JP225", "JPN225", "NI225", "NIKKEI225"),
    "SP35": ("SP35", "ES35", "IBEX35", "SPAIN35"),
    "UK100": ("UK100", "FTSE100", "UKX"),
    "US500": ("US500", "SPX500", "SP500", "USSPX500"),
    "US100": ("US100", "NAS100", "NDX", "USTECH100"),
    "US30": ("US30", "DJ30", "DJI", "DOW30", "USWALLST30"),
    "GOLD": ("GOLD", "XAUUSD", "XAU", "XAU/USD"),
    "BTCUSD": ("BTCUSD", "BTC/USD", "BITCOIN"),
}


def normalize_symbol_name(value: str | None) -> str:
    return NORMALIZE_RE.sub("", str(value or "").upper())


def aliases_for(local_symbol: str) -> tuple[str, ...]:
    local_symbol = local_symbol.upper()
    return LOCAL_ALIASES.get(local_symbol, (local_symbol,))


def _normalized_aliases(local_symbol: str) -> set[str]:
    return {normalize_symbol_name(item) for item in aliases_for(local_symbol)}


def score_remote_symbol(target: TargetSymbol, remote: RemoteSymbol) -> tuple[int, str]:
    """Return a conservative match score and reason for one candidate."""
    local_norm = normalize_symbol_name(target.local_symbol)
    remote_norm = normalize_symbol_name(remote.symbol_name)
    desc_norm = normalize_symbol_name(remote.description)
    aliases = _normalized_aliases(target.local_symbol)

    if remote_norm == local_norm:
        return 100, "exact local symbol"
    if remote_norm in aliases:
        return 96, "exact alias"
    if any(remote_norm.startswith(alias) for alias in aliases):
        return 88, "symbol starts with alias"
    if any(alias in remote_norm for alias in aliases):
        return 82, "symbol contains alias"
    if desc_norm and any(alias in desc_norm for alias in aliases):
        return 76, "description contains alias"
    return 0, "no reliable symbol signal"


def best_matches_for_target(
    target: TargetSymbol,
    remotes: list[RemoteSymbol],
    min_score: int = 80,
    ambiguity_margin: int = 5,
) -> SymbolMatch:
    ranked = sorted(
        ((score_remote_symbol(target, remote), remote) for remote in remotes),
        key=lambda item: item[0][0],
        reverse=True,
    )
    usable = [(score, reason, remote) for (score, reason), remote in ranked if score >= min_score]
    if not usable:
        alternatives = tuple(remote for (_pair, remote) in ranked[:5])
        return SymbolMatch(
            target=target,
            remote=None,
            score=0,
            status="UNMATCHED",
            reason="no candidate above threshold",
            alternatives=alternatives,
        )

    top_score, top_reason, top_remote = usable[0]
    second = usable[1] if len(usable) > 1 else None
    if second and second[0] >= top_score - ambiguity_margin:
        return SymbolMatch(
            target=target,
            remote=top_remote,
            score=top_score,
            status="AMBIGUOUS",
            reason=f"{top_reason}; close alternative {second[2].symbol_name}",
            alternatives=tuple(item[2] for item in usable[:5]),
        )

    return SymbolMatch(
        target=target,
        remote=top_remote,
        score=top_score,
        status="MATCHED",
        reason=top_reason,
        alternatives=tuple(item[2] for item in usable[1:5]),
    )


def build_symbol_matches(
    targets: tuple[TargetSymbol, ...],
    remotes: list[RemoteSymbol],
    min_score: int = 80,
    ambiguity_margin: int = 5,
) -> list[SymbolMatch]:
    return [
        best_matches_for_target(
            target,
            remotes,
            min_score=min_score,
            ambiguity_margin=ambiguity_margin,
        )
        for target in targets
    ]
