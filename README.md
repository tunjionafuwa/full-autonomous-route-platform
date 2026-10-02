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

## Road network modes

Select City, Rural, or Highway when generating a route. The first attempt uses only the selected OpenStreetMap `highway` classes. If an endpoint cannot be snapped or the selected network is disconnected, the app downloads a bounded corridor of drivable roads between the endpoints and retries. Fetched roads from other classes are available as connectors only when selected-mode roads still cannot connect the route.

- City: `living_street`, `residential`, `service`, `tertiary`, and `tertiary_link`
- Rural: `unclassified`, `tertiary`, `tertiary_link`, `secondary`, `secondary_link`, `primary`, and `primary_link`
- Highway: `motorway`, `motorway_link`, `trunk`, and `trunk_link`

These are road-class presets, not geographic urban/rural detection. Some classes, such as `tertiary`, can occur in more than one setting and are shared by the City and Rural presets.
Ad-hoc corridor downloads are buffered by 2 km and limited to endpoint pairs within 75 km.

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

- The graph loader retries a secondary Overpass endpoint and caches successfully downloaded city graphs as GraphML under `.cache/city-route/graphs/` for later outages. A first-time city load still requires an available Overpass endpoint.
- Edge identity operates direction-independently for physical street segments.
- Pattern scoring is intentionally heuristic and configurable.
