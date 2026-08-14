from __future__ import annotations

import heapq
import random
from dataclasses import dataclass
from typing import Optional

import networkx as nx

from city_route.config import RoutingConfig
from city_route.geometry.patterns import RoutePatternScorer
from city_route.models.route import RouteDecision, RouteResult


@dataclass
class SearchState:
    node: str
    cost: float
    path: list[str]
    used_edges: set[tuple[str, str]]
    reused_edges: set[tuple[str, str]]
    elapsed: float
    score: float = 0.0


class RoutePlanner:
    def __init__(self, config: RoutingConfig | None = None) -> None:
        self.config = config or RoutingConfig()
        self.pattern_scorer = RoutePatternScorer()
        
        # Set random seed for reproducibility if configured
        if self.config.random_seed is not None:
            random.seed(self.config.random_seed)

    @staticmethod
    def _edge_data(graph: nx.Graph, u: str, v: str) -> dict:
        raw = graph.get_edge_data(u, v, default={})
        if not raw:
            return {}
        if isinstance(graph, nx.MultiDiGraph):
            candidates = [
                (float(item.get("travel_time", item.get("length", float("inf")))), item)
                for item in raw.values()
                if isinstance(item, dict)
            ]
            if candidates:
                return min(candidates, key=lambda item: item[0])[1]
            return {}
        if isinstance(raw, dict):
            return raw
        return {}

    @staticmethod
    def _travel_time(graph: nx.Graph, u: str, v: str) -> float:
        edge_data = RoutePlanner._edge_data(graph, u, v)
        if edge_data:
            return float(edge_data.get("travel_time", edge_data.get("length", 1.0)))
        raw = graph.get_edge_data(u, v, default={})
        if isinstance(graph, nx.MultiDiGraph):
            candidates = [
                (float(item.get("travel_time", item.get("length", float("inf")))), item)
                for item in raw.values()
                if isinstance(item, dict)
            ]
            if candidates:
                return float(min(candidates, key=lambda item: item[0])[1].get("travel_time", min(candidates, key=lambda item: item[0])[1].get("length", 1.0)))
        if isinstance(raw, dict):
            return float(raw.get("travel_time", raw.get("length", 1.0)))
        return 1.0

    def route(self, graph: nx.Graph, start: str, end: str) -> RouteResult:
        if start not in graph or end not in graph:
            return RouteResult(
                nodes=[start],
                edges=[],
                distance_m=0.0,
                duration_seconds=0.0,
                pattern_score=0.0,
                reused_edges=[],
                reused_edge_count=0,
                reached_destination=False,
                within_time_budget=True,
                loop=start == end,
                feasible=False,
            )

        if start == end:
            return self._route_loop(graph, start)
        return self._route_point_to_point_with_target(graph, start, end)

    def _route_point_to_point_with_target(self, graph: nx.Graph, start: str, end: str) -> RouteResult:
        """Route from start to end, targeting a specific duration with dynamic search."""
        target_seconds = self.config.target_time_seconds
        max_seconds = self.config.max_time_seconds
        tolerance_ratio = self.config.time_tolerance_ratio
        tolerance_min = self.config.time_tolerance_min_seconds

        # Find shortest feasible path first
        shortest_path = self._shortest_feasible_path(graph, start, end)
        if shortest_path is None:
            return RouteResult(
                nodes=[start],
                edges=[],
                distance_m=0.0,
                duration_seconds=0.0,
                pattern_score=0.0,
                reused_edges=[],
                reused_edge_count=0,
                reached_destination=False,
                within_time_budget=False,
                feasible=False,
            )

        shortest_time = self._estimate_path_time(graph, shortest_path)
        if shortest_time is None or shortest_time <= 0:
            shortest_time = sum(self._travel_time(graph, shortest_path[i], shortest_path[i+1]) 
                              for i in range(len(shortest_path)-1))

        # Calculate dynamic tolerance based on target
        # Primary: use ratio of target time (5% by default)
        # Minimum: at least 5 seconds for floating-point precision
        min_tolerance = max(5.0, target_seconds * tolerance_ratio)
        
        # If shortest path is already very close to target, return it
        if abs(shortest_time - target_seconds) <= min_tolerance:
            return self._build_result(graph, shortest_path, set(), set(), shortest_time, [], 
                                    reached_destination=True)

        # If target is impossible (shorter than shortest path), return shortest
        if target_seconds < shortest_time * 0.95:  # 5% tolerance for floating point
            return self._build_result(graph, shortest_path, set(), set(), shortest_time, [], 
                                    reached_destination=True)

        # DYNAMIC TARGET-TIME SEARCH: Generate diverse candidates iteratively
        best_route, best_time, best_error = shortest_path, shortest_time, abs(shortest_time - target_seconds)
        visited_routes = {self._route_signature(shortest_path)}
        
        max_iterations = int(self.config.max_search_steps * 1.5)  # Increased iterations for aggressive search
        iteration = 0
        iterations_without_improvement = 0
        max_no_improve = 20
        
        while iteration < max_iterations:
            iteration += 1
            
            # Generate diverse candidates using multiple strategies
            candidates = self._generate_diverse_candidates(
                graph, start, end, shortest_path, target_seconds, 
                max_seconds, visited_routes, iteration
            )
            
            if not candidates:
                # No new candidates found
                iterations_without_improvement += 1
                if iterations_without_improvement >= max_no_improve:
                    break
                continue
            
            iterations_without_improvement = 0
            
            # Evaluate all candidates and track the best
            found_improvement = False
            for candidate_path in candidates:
                candidate_time = self._estimate_path_time(graph, candidate_path)
                if candidate_time is None or candidate_time <= 0:
                    continue
                
                # Skip if too far over max time
                if candidate_time > max_seconds * 1.2:
                    continue
                
                # Add to visited to prevent reuse
                route_sig = self._route_signature(candidate_path)
                if route_sig in visited_routes:
                    continue
                visited_routes.add(route_sig)
                
                # Check if this candidate is better
                candidate_error = abs(candidate_time - target_seconds)
                if candidate_error < best_error:
                    best_route, best_time, best_error = candidate_path, candidate_time, candidate_error
                    found_improvement = True
                    
                    # If we've reached target tolerance, return immediately
                    if candidate_error <= min_tolerance:
                        return self._build_result(graph, best_route, set(), set(), best_time, [], 
                                                reached_destination=True)
            
            # If we found improvements, can continue searching
            # But if error is still high (> 20%), be less aggressive about stopping
            if best_error <= min_tolerance or (best_error <= target_seconds * 0.10 and found_improvement):
                break
        
        return self._build_result(graph, best_route, set(), set(), best_time, [], 
                                reached_destination=True)

    def _generate_diverse_candidates(
        self,
        graph: nx.Graph,
        start: str,
        end: str,
        shortest_path: list[str],
        target_seconds: float,
        max_seconds: float,
        visited_routes: set[str],
        iteration: int
    ) -> list[list[str]]:
        """Generate diverse candidate routes using multiple strategies."""
        candidates = []
        current_time = self._estimate_path_time(graph, shortest_path)
        
        # If already at target, no need for more candidates
        if abs(current_time - target_seconds) <= target_seconds * 0.05:
            return []
        
        # Calculate how much longer the route needs to be
        expansion_factor = target_seconds / current_time if current_time > 0 else 1.0
        
        # Strategy 1: Multi-waypoint expansion (most aggressive for long durations)
        if expansion_factor > 1.5:  # Only if target is significantly longer
            multi_waypoint_candidates = self._generate_multi_waypoint_routes(
                graph, start, end, shortest_path, target_seconds, max_seconds, expansion_factor
            )
            candidates.extend(multi_waypoint_candidates)
        
        # Strategy 2: Random walk with explicit duration budget
        if len(candidates) < 5:
            random_walk_candidates = self._generate_duration_guided_walk(
                graph, start, end, shortest_path, target_seconds, max_seconds
            )
            candidates.extend(random_walk_candidates)
        
        # Strategy 3: Directional exploration (explore in different compass directions)
        if len(candidates) < 8:
            directional_candidates = self._generate_directional_exploration_routes(
                graph, start, end, shortest_path, target_seconds, max_seconds
            )
            candidates.extend(directional_candidates)
        
        # Strategy 4: K-shortest paths (fallback for finding alternatives)
        if len(candidates) < 10:
            alt_candidates = self._generate_alternative_paths(
                graph, start, end, shortest_path, max_seconds, k=5
            )
            candidates.extend(alt_candidates)
        
        # Filter out duplicates
        unique_candidates = []
        seen_sigs = set(visited_routes)
        for candidate in candidates:
            sig = self._route_signature(candidate)
            if sig not in seen_sigs and len(candidate) > 0:
                unique_candidates.append(candidate)
                seen_sigs.add(sig)
        
        return unique_candidates

    def _generate_multi_waypoint_routes(
        self,
        graph: nx.Graph,
        start: str,
        end: str,
        shortest_path: list[str],
        target_seconds: float,
        max_seconds: float,
        expansion_factor: float
    ) -> list[list[str]]:
        """Generate routes with multiple intermediate waypoints to expand duration."""
        candidates = []
        
        # Determine number of waypoints based on how much longer we need
        # More waypoints = longer routes
        base_waypoints = max(2, int(expansion_factor))
        num_waypoints = min(8, base_waypoints + 1)  # Allow more waypoints for very long routes
        
        # For very long routes (>2x shortest), try even more waypoints
        if expansion_factor > 3.0:
            num_waypoints = min(10, num_waypoints + 2)
        
        # Select waypoints spread across the network
        waypoint_candidates = self._select_diverse_waypoints(
            graph, start, end, shortest_path, num_waypoints
        )
        
        if not waypoint_candidates:
            return []
        
        # Try building routes through different waypoint sequences
        for waypoint_seq in waypoint_candidates:
            try:
                # Build route: start -> waypoint[0] -> waypoint[1] -> ... -> waypoint[n] -> end
                path_segments = [start]
                current = start
                
                for waypoint in waypoint_seq:
                    segment = self._shortest_feasible_path(graph, current, waypoint)
                    if not segment:
                        break
                    path_segments.extend(segment[1:])  # Skip duplicate node
                    current = waypoint
                else:
                    # Successfully built all segments to waypoints, now go to end
                    final_segment = self._shortest_feasible_path(graph, current, end)
                    if final_segment:
                        path_segments.extend(final_segment[1:])
                        
                        total_time = self._estimate_path_time(graph, path_segments)
                        if total_time and total_time <= max_seconds:
                            candidates.append(path_segments)
            except Exception:
                continue
        
        return candidates

    def _select_diverse_waypoints(
        self,
        graph: nx.Graph,
        start: str,
        end: str,
        shortest_path: list[str],
        num_waypoints: int
    ) -> list[list[str]]:
        """Select diverse waypoints spread across the network."""
        waypoint_sequences = []
        
        # Strategy: sample nodes at different "levels" of distance from start
        # This ensures we explore in expanding rings around the start
        
        shortest_dist = {}
        try:
            shortest_dist = nx.single_source_dijkstra_path_length(
                graph, start, weight="travel_time"
            )
        except Exception:
            return []
        
        # Get nodes at different distance tiers
        max_dist = max(shortest_dist.values()) if shortest_dist else 0
        if max_dist == 0:
            return []
        
        # Create more tiers for longer searches
        num_tiers = max(num_waypoints, 3)
        tiers = []
        
        for tier_idx in range(num_tiers):
            tier_ratio = (tier_idx + 1) / num_tiers
            tier_distance = max_dist * tier_ratio
            
            # Find nodes close to this distance, with wider tolerance
            candidates_for_tier = [
                node for node, dist in shortest_dist.items()
                if abs(dist - tier_distance) < max_dist * 0.20  # 20% tolerance
                and node not in shortest_path  # Not on direct path
            ]
            
            if candidates_for_tier:
                # Take more candidates per tier for better selection
                tiers.append(random.sample(
                    candidates_for_tier,
                    min(5, len(candidates_for_tier))
                ))
        
        # If we have enough tiers, create multiple waypoint sequences
        if tiers:
            # Generate several different sequences
            for attempt in range(8):  # Try 8 different waypoint sequences
                waypoint_seq = []
                for tier_idx in range(min(num_waypoints, len(tiers))):
                    if tiers[tier_idx]:
                        waypoint_seq.append(random.choice(tiers[tier_idx]))
                
                if len(waypoint_seq) == num_waypoints:
                    waypoint_sequences.append(waypoint_seq)
        
        return waypoint_sequences

    def _generate_duration_guided_walk(
        self,
        graph: nx.Graph,
        start: str,
        end: str,
        shortest_path: list[str],
        target_seconds: float,
        max_seconds: float
    ) -> list[list[str]]:
        """Generate routes using guided random walks with duration targets."""
        candidates = []
        shortest_time = self._estimate_path_time(graph, shortest_path)
        
        for attempt in range(4):  # 4 different walks
            current = start
            path = [current]
            current_time = 0.0
            visited_local = {current}
            
            max_steps = min(500, self.config.max_search_steps)
            steps = 0
            
            while steps < max_steps and current_time < max_seconds * 0.9:
                # Calculate remaining time budget
                remaining_budget = target_seconds - current_time
                
                # Get neighbors with scoring
                neighbors = list(graph.neighbors(current))
                if not neighbors:
                    break
                
                scored_neighbors = []
                for neighbor in neighbors:
                    edge_time = self._travel_time(graph, current, neighbor)
                    
                    # Skip if it would make us way over target
                    if current_time + edge_time > max_seconds:
                        continue
                    
                    # Score based on:
                    # 1. Whether it keeps us roughly on track for target time
                    # 2. Diversity (prefer less-visited nodes)
                    time_goodness = 1.0 - abs(current_time + edge_time - target_seconds * (steps + 1) / max_steps) / target_seconds
                    diversity_bonus = 0.5 if neighbor not in visited_local else -1.0
                    randomness = random.random() * 0.3
                    
                    score = time_goodness + diversity_bonus + randomness
                    scored_neighbors.append((score, neighbor, edge_time))
                
                if not scored_neighbors:
                    break
                
                # Pick best scored neighbor
                scored_neighbors.sort(reverse=True)
                _, best_neighbor, edge_time = scored_neighbors[0]
                
                current_time += edge_time
                current = best_neighbor
                path.append(current)
                visited_local.add(current)
                steps += 1
                
                # If we're in good range to target, prepare to close to end
                if target_seconds * 0.80 <= current_time <= target_seconds * 1.1:
                    try:
                        final_path = nx.shortest_path(graph, current, end, weight="travel_time")
                        complete_path = path[:-1] + final_path
                        complete_time = self._estimate_path_time(graph, complete_path)
                        if complete_time and complete_time <= max_seconds:
                            candidates.append(complete_path)
                    except (nx.NetworkXNoPath, nx.NodeNotFound):
                        pass
                    break
            
            # If walk completed without hitting target, close to end anyway
            if current != end:
                try:
                    final_path = nx.shortest_path(graph, current, end, weight="travel_time")
                    complete_path = path[:-1] + final_path
                    complete_time = self._estimate_path_time(graph, complete_path)
                    if complete_time and complete_time <= max_seconds:
                        candidates.append(complete_path)
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    pass
        
        return candidates

    def _generate_directional_exploration_routes(
        self,
        graph: nx.Graph,
        start: str,
        end: str,
        shortest_path: list[str],
        target_seconds: float,
        max_seconds: float
    ) -> list[list[str]]:
        """Generate routes that explore in different compass directions."""
        candidates = []
        
        # Get coordinates if available
        start_node_data = graph.nodes.get(start, {})
        end_node_data = graph.nodes.get(end, {})
        
        start_x = float(start_node_data.get("x", 0))
        start_y = float(start_node_data.get("y", 0))
        end_x = float(end_node_data.get("x", 0))
        end_y = float(end_node_data.get("y", 0))
        
        # Define directions: North, South, East, West, and combinations
        directions = [
            (0, 1),   # North
            (0, -1),  # South
            (1, 0),   # East
            (-1, 0),  # West
            (1, 1),   # Northeast
            (-1, -1), # Southwest
            (1, -1),  # Southeast
            (-1, 1),  # Northwest
        ]
        
        # For each direction, find nodes in that direction and build routes through them
        for dx, dy in directions[:4]:  # Limit to primary 4 directions for speed
            # Find nodes in this direction from start
            directional_nodes = []
            for node in graph.nodes():
                node_data = graph.nodes.get(node, {})
                node_x = float(node_data.get("x", 0))
                node_y = float(node_data.get("y", 0))
                
                # Check if this node is roughly in the desired direction
                if dx > 0 and node_x < start_x:  # Looking east but node is west
                    continue
                if dx < 0 and node_x > start_x:  # Looking west but node is east
                    continue
                if dy > 0 and node_y < start_y:  # Looking north but node is south
                    continue
                if dy < 0 and node_y > start_y:  # Looking south but node is north
                    continue
                
                if node not in shortest_path:
                    directional_nodes.append(node)
            
            # Sample a few nodes from this direction
            if directional_nodes:
                sampled = random.sample(directional_nodes, min(3, len(directional_nodes)))
                
                for waypoint in sampled:
                    try:
                        path_to_waypoint = self._shortest_feasible_path(graph, start, waypoint)
                        path_from_waypoint = self._shortest_feasible_path(graph, waypoint, end)
                        
                        if path_to_waypoint and path_from_waypoint:
                            route = path_to_waypoint[:-1] + path_from_waypoint
                            route_time = self._estimate_path_time(graph, route)
                            
                            if route_time and route_time <= max_seconds:
                                candidates.append(route)
                    except Exception:
                        continue
        
        return candidates

    def _expand_route_greedily(
        self,
        graph: nx.Graph,
        start: str,
        end: str,
        shortest_path: list[str],
        target_seconds: float,
        max_seconds: float
    ) -> list[list[str]]:
        """Greedily expand the shortest path to meet target duration."""
        candidates = []
        current_time = self._estimate_path_time(graph, shortest_path)
        
        if current_time >= target_seconds * 0.95:
            # Already close to target
            return []  # Empty list signals we should try other strategies
        
        # Need to expand the route
        # Try simple detours first (most reliable)
        for insert_idx in range(1, len(shortest_path)):
            before_node = shortest_path[insert_idx - 1]
            at_node = shortest_path[insert_idx]
            
            # Try all neighbors of the node we're currently at
            for detour_candidate in list(graph.neighbors(at_node)):
                # Skip nodes already on the path
                if detour_candidate in shortest_path:
                    continue
                
                try:
                    # Path: ...before_node -> detour -> at_node...
                    path_via_detour = self._shortest_feasible_path(graph, before_node, at_node)
                    if not path_via_detour:
                        continue
                    
                    # Try going via detour
                    alt_path = self._shortest_feasible_path(graph, before_node, detour_candidate)
                    path_back = self._shortest_feasible_path(graph, detour_candidate, at_node)
                    
                    if alt_path and path_back:
                        # Build detour: prev + via_detour + rest
                        new_path = (
                            shortest_path[:insert_idx] +
                            alt_path[1:-1] +  # Skip overlaps
                            path_back[1:] +
                            shortest_path[insert_idx + 1:]
                        )
                        
                        new_time = self._estimate_path_time(graph, new_path)
                        if new_time and new_time > current_time and new_time <= max_seconds:
                            candidates.append((new_time, new_path))
                except Exception:
                    continue
        
        # Sort candidates by time to get the best expansions
        candidates.sort(key=lambda x: abs(x[0] - target_seconds))
        
        return [path for _, path in candidates[:10]]  # Return top 10 candidates

    def _expand_with_multiple_detours(
        self,
        graph: nx.Graph,
        shortest_path: list[str],
        target_seconds: float,
        max_seconds: float
    ) -> list[list[str]]:
        """Try adding multiple detours to expand the route."""
        candidates = []
        current_path = shortest_path[:]
        current_time = self._estimate_path_time(graph, current_path)
        
        attempts = 0
        max_detour_attempts = 3
        
        while current_time < target_seconds * 0.9 and attempts < max_detour_attempts:
            attempts += 1
            # Pick a random insertion point
            insert_idx = random.randint(1, len(current_path) - 1)
            insert_node = current_path[insert_idx]
            
            nearby = self._get_nearby_nodes(
                graph, insert_node, max_depth=2, exclude=set(current_path)
            )
            
            if not nearby:
                break
            
            detour_node = random.choice(list(nearby))
            
            try:
                path_to = self._shortest_feasible_path(graph, current_path[insert_idx - 1], detour_node)
                path_from = self._shortest_feasible_path(graph, detour_node, insert_node)
                
                if path_to and path_from:
                    new_path = (
                        current_path[:insert_idx] +
                        path_to[1:-1] +  # Skip endpoints to avoid duplication
                        path_from[1:]    # Skip start point
                    )
                    new_time = self._estimate_path_time(graph, new_path)
                    
                    if new_time and new_time <= max_seconds:
                        candidates.append(new_path)
                        current_path = new_path
                        current_time = new_time
                    else:
                        break
            except Exception:
                break
        
        return candidates

    def _generate_random_walk_candidates(
        self,
        graph: nx.Graph,
        start: str,
        end: str,
        shortest_path: list[str],
        target_seconds: float,
        max_seconds: float,
        iteration: int
    ) -> list[list[str]]:
        """Generate candidates using controlled random walks."""
        candidates = []
        
        for attempt in range(3):  # 3 random walk attempts
            current = start
            path = [current]
            current_time = 0.0
            visited_local = {current}  # Local visit tracking for this walk
            
            max_steps = self.config.max_search_steps // 10  # Use smaller budget per walk
            steps = 0
            
            while steps < max_steps and current_time < max_seconds * 0.9:
                # Bias toward neighbors that move us toward end
                # but with some randomness to explore alternatives
                neighbors = list(graph.neighbors(current))
                
                if not neighbors:
                    break
                
                # Score neighbors: prefer those moving toward end, but allow detours
                best_neighbor = None
                best_score = -float('inf')
                
                for neighbor in neighbors:
                    # Calculate time if we go to this neighbor
                    edge_time = self._travel_time(graph, current, neighbor)
                    
                    # Prefer neighbors that keep us under budget
                    if current_time + edge_time > max_seconds:
                        continue
                    
                    # Bonus for moving toward destination
                    try:
                        dist_to_end_before = nx.shortest_path_length(
                            graph, current, end, weight="travel_time"
                        )
                        dist_to_end_after = nx.shortest_path_length(
                            graph, neighbor, end, weight="travel_time"
                        )
                        progress_bonus = (dist_to_end_before - dist_to_end_after) / edge_time if edge_time > 0 else 0
                    except nx.NetworkXNoPath:
                        progress_bonus = 0
                    
                    # Randomness factor
                    randomness = random.random() * 0.3
                    
                    score = progress_bonus + randomness
                    if score > best_score:
                        best_score = score
                        best_neighbor = neighbor
                
                if best_neighbor is None:
                    break
                
                edge_time = self._travel_time(graph, current, best_neighbor)
                current_time += edge_time
                current = best_neighbor
                path.append(current)
                visited_local.add(current)
                steps += 1
                
                # Check if we're close to target, then head to end
                if target_seconds * 0.85 <= current_time <= target_seconds * 1.1:
                    try:
                        remaining_path = nx.shortest_path(
                            graph, current, end, weight="travel_time"
                        )
                        final_time = self._estimate_path_time(graph, path[:-1] + remaining_path)
                        if final_time and final_time <= max_seconds:
                            candidates.append(path[:-1] + remaining_path)
                    except nx.NetworkXNoPath:
                        pass
                    break
            
            # If we completed a walk, try to close it to destination
            if current != end:
                try:
                    closing_path = nx.shortest_path(
                        graph, current, end, weight="travel_time"
                    )
                    final_path = path[:-1] + closing_path
                    final_time = self._estimate_path_time(graph, final_path)
                    if final_time and final_time <= max_seconds:
                        candidates.append(final_path)
                except nx.NetworkXNoPath:
                    pass
        
        return candidates

    def _route_signature(self, path: list[str]) -> str:
        """Create a signature for a route to detect duplicates."""
        # Use edge set for signature (order-independent for cycle detection)
        edges = []
        for i in range(len(path) - 1):
            u, v = str(path[i]), str(path[i + 1])
            edges.append(f"{u}→{v}")
        return "|".join(edges)

    def _generate_perturbed_candidates(
        self,
        graph: nx.Graph,
        start: str,
        end: str,
        target_seconds: float,
        max_seconds: float,
        iteration: int
    ) -> list[list[str]]:
        """Generate candidates by perturbing edge weights."""
        candidates = []
        
        # Create a copy of the graph for weight manipulation
        perturbed_graph = graph.copy()
        
        # Try multiple perturbation strategies
        for strategy_idx in range(3):  # 3 different perturbation strategies
            # Create unique perturbation factors for each strategy
            # Strategy 0: slightly perturb all edges (small randomization)
            # Strategy 1: heavily perturb some edges (explore far from shortest path)
            # Strategy 2: inverse perturbation (favor previously slower paths)
            
            # Handle both MultiDiGraph and regular Graph
            if isinstance(perturbed_graph, nx.MultiDiGraph):
                edges_to_process = perturbed_graph.edges(keys=True, data=True)
            else:
                edges_to_process = [(u, v, 0, d) for u, v, d in perturbed_graph.edges(data=True)]
            
            for u, v, key, data in edges_to_process:
                if strategy_idx == 0:
                    # Slight randomization: multiply by 0.9-1.1
                    factor = 0.9 + random.random() * 0.2
                elif strategy_idx == 1:
                    # Heavy perturbation: multiply by 0.5-2.0
                    factor = 0.5 + random.random() * 1.5
                else:  # strategy_idx == 2
                    # Inverse: favor longer paths
                    factor = 1.0 + random.random() * 0.5
                
                old_time = float(data.get("travel_time", 1.0))
                new_time = old_time * factor
                data["travel_time"] = new_time
            
            # Find shortest path in perturbed graph
            try:
                perturbed_path = nx.shortest_path(
                    perturbed_graph, start, end, weight="travel_time"
                )
                candidates.append(perturbed_path)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                pass
        
        return candidates

    def _generate_alternative_paths(
        self,
        graph: nx.Graph,
        start: str,
        end: str,
        shortest_path: list[str],
        max_seconds: float,
        k: int = 8
    ) -> list[list[str]]:
        """Generate alternative paths by excluding edges from previous solutions."""
        candidates = []
        excluded_edges = set()
        
        for iteration_num in range(k):
            # Create graph without excluded edges
            working_graph = graph.copy()
            
            # Remove excluded edges
            for u, v in excluded_edges:
                if working_graph.has_edge(u, v):
                    if isinstance(working_graph, nx.MultiDiGraph):
                        keys_to_remove = list(working_graph[u][v].keys())
                        for key in keys_to_remove:
                            try:
                                working_graph.remove_edge(u, v, key)
                            except:
                                pass
                    else:
                        try:
                            working_graph.remove_edge(u, v)
                        except:
                            pass
            
            # Also try removing reverse edges (for undirected)
            for u, v in excluded_edges:
                if working_graph.has_edge(v, u):
                    if isinstance(working_graph, nx.MultiDiGraph):
                        keys_to_remove = list(working_graph[v][u].keys())
                        for key in keys_to_remove:
                            try:
                                working_graph.remove_edge(v, u, key)
                            except:
                                pass
                    else:
                        try:
                            working_graph.remove_edge(v, u)
                        except:
                            pass
            
            # Find shortest path in modified graph
            try:
                alt_path = nx.shortest_path(working_graph, start, end, weight="travel_time")
                alt_time = self._estimate_path_time(graph, alt_path)
                
                if alt_time and alt_time <= max_seconds:
                    candidates.append(alt_path)
                    
                    # Add edges from this path to excluded set for next iteration
                    # This encourages finding different paths
                    for i in range(len(alt_path) - 1):
                        u, v = alt_path[i], alt_path[i + 1]
                        excluded_edges.add((u, v))
                        excluded_edges.add((v, u))  # Also exclude reverse
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                # No path found without these edges, stop trying
                break
        
        return candidates

    def _generate_waypoint_routes_adaptive(
        self,
        graph: nx.Graph,
        start: str,
        end: str,
        shortest_path: list[str],
        target_seconds: float,
        max_seconds: float,
        iteration: int
    ) -> list[list[str]]:
        """Generate routes through adaptive waypoint selection."""
        candidates = []
        
        if len(shortest_path) < 3:
            return candidates
        
        # Try waypoints at different proportions along the shortest path
        for waypoint_ratio in [0.25, 0.33, 0.5, 0.67, 0.75]:
            waypoint_idx = max(1, int(len(shortest_path) * waypoint_ratio))
            if waypoint_idx >= len(shortest_path) - 1:
                continue
            
            waypoint_node = shortest_path[waypoint_idx]
            
            # Get nearby nodes (reachable within reasonable distance)
            nearby_nodes = self._get_nearby_nodes(
                graph, waypoint_node, 
                max_depth=3 + (iteration % 3),  # Adaptive depth based on iteration
                exclude=set(shortest_path)
            )
            
            if not nearby_nodes:
                continue
            
            # Try each nearby node as a detour waypoint
            for detour_node in list(nearby_nodes)[:8]:  # Limit to 8 per waypoint
                try:
                    # Path: start -> detour_node -> waypoint_node -> end
                    path_1 = self._shortest_feasible_path(graph, start, detour_node)
                    path_2 = self._shortest_feasible_path(graph, detour_node, waypoint_node)
                    path_3 = self._shortest_feasible_path(graph, waypoint_node, end)
                    
                    if path_1 and path_2 and path_3:
                        combined = path_1[:-1] + path_2[:-1] + path_3
                        total_time = self._estimate_path_time(graph, combined)
                        
                        if total_time and total_time <= max_seconds:
                            candidates.append(combined)
                except Exception:
                    continue
        
        return candidates

    def _generate_loop_back_routes_diverse(
        self,
        graph: nx.Graph,
        start: str,
        end: str,
        shortest_path: list[str],
        target_seconds: float,
        max_seconds: float,
        iteration: int
    ) -> list[list[str]]:
        """Generate diverse loop-back routes with different detour points."""
        candidates = []
        
        if len(shortest_path) < 3:
            return candidates
        
        # Try multiple loop-back positions
        for loop_idx in range(1, len(shortest_path) - 1):
            loop_node = shortest_path[loop_idx]
            
            # Get nearby nodes for the loop-back detour
            nearby = self._get_nearby_nodes(
                graph, loop_node,
                max_depth=2 + (iteration % 2),
                exclude=set(shortest_path)
            )
            
            if not nearby:
                continue
            
            # Try each nearby node as a loop-back point
            for detour_node in list(nearby)[:5]:  # Limit to 5 per loop point
                try:
                    path_1 = self._shortest_feasible_path(graph, start, loop_node)
                    path_2 = self._shortest_feasible_path(graph, loop_node, detour_node)
                    path_3 = self._shortest_feasible_path(graph, detour_node, loop_node)
                    path_4 = self._shortest_feasible_path(graph, loop_node, end)
                    
                    if path_1 and path_2 and path_3 and path_4:
                        # start -> loop_node -> detour -> loop_node -> end
                        combined = path_1[:-1] + path_2[:-1] + path_3[:-1] + path_4
                        total_time = self._estimate_path_time(graph, combined)
                        
                        if total_time and total_time <= max_seconds:
                            candidates.append(combined)
                except Exception:
                    continue
        
        return candidates

    def _get_nearby_nodes(
        self, 
        graph: nx.Graph, 
        start_node: str, 
        max_depth: int = 3,
        exclude: set[str] | None = None
    ) -> set[str]:
        """Get nodes reachable from start_node within max_depth edges, excluding specific nodes."""
        if exclude is None:
            exclude = set()
        
        visited = set()
        queue = [(start_node, 0)]
        result = set()
        
        while queue:
            node, depth = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            
            if node != start_node and node not in exclude:
                result.add(node)
            
            if depth < max_depth:
                for neighbor in graph.neighbors(node):
                    if neighbor not in visited:
                        queue.append((neighbor, depth + 1))
        
        return result

    def _route_loop(self, graph: nx.Graph, start: str) -> RouteResult:
        """Generate a loop route targeting a specific duration."""
        target_seconds = self.config.target_time_seconds
        max_seconds = self.config.max_time_seconds

        path = [start]
        current = start
        used_edges: set[tuple[str, str]] = set()
        reused_edges: set[tuple[str, str]] = set()
        total_duration = 0.0
        visited = {start}
        step_count = 0

        while step_count < self.config.max_search_steps and total_duration < max_seconds:
            choices = []
            for neighbor in graph.neighbors(current):
                edge_key = self._edge_key(current, neighbor)
                if edge_key in used_edges and (not self.config.allow_reuse or len(reused_edges) >= self.config.max_reuse_count):
                    continue
                edge_data = self._edge_data(graph, current, neighbor)
                fallback = self._travel_time(graph, current, neighbor)
                travel_time = float(edge_data.get("travel_time", fallback))
                if total_duration + travel_time > max_seconds:
                    continue
                
                score = self._path_score(graph, path + [neighbor])
                
                remaining_time = target_seconds - (total_duration + travel_time)
                time_error = abs(remaining_time)
                
                choices.append((score, -time_error, travel_time, neighbor))
            
            if not choices:
                break

            _, _, _, best_neighbor = max(choices, key=lambda item: (item[0], item[1]))
            
            edge_key = self._edge_key(current, best_neighbor)
            if edge_key in used_edges:
                reused_edges.add(edge_key)
            else:
                used_edges.add(edge_key)
            
            edge_data = self._edge_data(graph, current, best_neighbor)
            travel_time = float(edge_data.get("travel_time", self._travel_time(graph, current, best_neighbor)))
            total_duration += travel_time
            current = best_neighbor
            path.append(current)
            visited.add(current)
            step_count += 1
            
            if total_duration >= target_seconds * 0.95:
                if current != start:
                    return_path = self._shortest_feasible_path(graph, current, start)
                    if return_path:
                        path = path[:-1] + return_path
                        total_duration = self._estimate_path_time(graph, path)
                break

        result = self._build_result(graph, path, used_edges, reused_edges, total_duration, [], reached_destination=True, loop=True)
        result.within_time_budget = result.duration_seconds <= max_seconds
        return result

    def _edge_key(self, u: str, v: str) -> tuple[str, str]:
        return tuple(sorted((str(u), str(v))))

    def _heuristic(self, graph: nx.Graph, node: str, destination: str) -> float:
        try:
            return nx.shortest_path_length(graph, node, destination, weight="travel_time")
        except nx.NetworkXNoPath:
            return 1e9

    def _path_score(self, graph: nx.Graph, path: list[str]) -> float:
        if len(path) < 3:
            return 0.0
        score = self.pattern_scorer.score(path, graph)
        return score * self.config.pattern_weight

    def _estimate_path_time(self, graph: nx.Graph, path: list[str]) -> float:
        if len(path) < 2:
            return 0.0
        total = 0.0
        for i in range(len(path) - 1):
            edge_data = self._edge_data(graph, path[i], path[i + 1])
            if not edge_data:
                total += 1.0
                continue
            total += float(edge_data.get("travel_time", 1.0))
        return total

    def _shortest_feasible_path(self, graph: nx.Graph, start: str, end: str) -> list[str] | None:
        try:
            return nx.shortest_path(graph, start, end, weight="travel_time")
        except nx.NetworkXNoPath:
            return None

    def _build_result(
        self,
        graph: nx.Graph,
        path: list[str],
        used_edges: set[tuple[str, str]],
        reused_edges: set[tuple[str, str]],
        duration: float,
        explanation: list[RouteDecision],
        reached_destination: bool,
        loop: bool = False,
    ) -> RouteResult:
        edge_names: list[str] = []
        for i in range(len(path) - 1):
            edge_names.append(f"{path[i]}->{path[i + 1]}")
        pattern_score = self.pattern_scorer.score(path, graph)
        distance_m = 0.0
        for u, v in zip(path, path[1:]):
            edge_data = self._edge_data(graph, u, v)
            if edge_data:
                distance_m += float(edge_data.get("length", 0.0))
        return RouteResult(
            nodes=path,
            edges=edge_names,
            distance_m=distance_m,
            duration_seconds=duration,
            pattern_score=pattern_score,
            reused_edges=[f"{u}->{v}" for u, v in reused_edges],
            reused_edge_count=len(reused_edges),
            reached_destination=reached_destination,
            within_time_budget=duration <= self.config.max_time_seconds,
            loop=loop,
            explanation=explanation,
            elapsed_time=duration,
            feasible=True,
        )
