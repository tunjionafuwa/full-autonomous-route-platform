# City Route Pattern Generator

This repository provides a prototype city routing application built around a time-constrained, pattern-aware route search. It is designed as a layered architecture: the routing engine is independent from the Streamlit UI and can be used from Python tests or scripts.

## Architecture

- `app.py`: Streamlit entry point
- `src/city_route/config.py`: central routing configuration
- `src/city_route/graph/`: OSMnx graph loading and preprocessing
- `src/city_route/geometry/`: geometry and pattern scoring utilities
- `src/city_route/routing/`: search and route planner
- `src/city_route/models/`: route dataclasses
- `tests/`: small synthetic graph tests

## Routing strategy

The prototype uses a bounded beam-style search over candidate steps, prioritizing:

1. Remaining time feasibility
2. Pattern-aware turn and shape scoring
3. Destination progress
4. Controlled edge reuse only when necessary

This keeps the algorithm explainable and testable, while avoiding a full-blown city-scale combinatorial search.

## Quick start

```bash
uv sync
uv run streamlit run app.py
```

## Testing

```bash
uv run pytest
```

## Notes

- The graph loader uses OSMnx and can be replaced with a cached offline dataset in production.
- Edge identity operates direction-independently for physical street segments.
- Pattern scoring is intentionally heuristic and configurable.
