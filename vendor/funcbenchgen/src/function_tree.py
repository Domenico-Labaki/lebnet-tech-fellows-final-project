import random
import string
from typing import List, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict
import networkx as nx
import numpy as np

@dataclass
class Function:
    name: str
    inputs: List[str]
    output: str
    description: str

class FunctionDependencyTree:
    def __init__(self, seed: Optional[int] = None):
        if seed is None:
            seed = random.choice(range(100000))
        
        self.seed = seed
        random.seed(self.seed)
        
        self.used_names = set()
        self.graph = nx.DiGraph()
        self.has_been_reformatted = False
        self.desired_output_variable: Optional[str] = None

        self.total_nodes = None
        self.min_calls = None
        self.max_critical_path = None
        self.disconnected_nodes = None


    def _generate_random_name(self, prefix: str = "") -> str:
        # Generate a random name that hasn't been used yet.
        while True:
            if prefix:
                name = prefix + "_" + ''.join(random.choices(string.ascii_lowercase, k=3))
            else:
                name = ''.join(random.choices(string.ascii_lowercase, k=random.randint(3, 6)))
            
            if name not in self.used_names:
                self.used_names.add(name)
                return name

    def _create_node(self, inputs: List[str], prefix: str = "func", node_type: str = 'core') -> str:
        # Create a new function node and add it to the graph.
        name = self._generate_random_name(prefix)
        output = self._generate_random_name()
        func = Function(name=name, inputs=inputs, output=output, description="")
        self.graph.add_node(name, function=func, node_type=node_type)
        return name
    
    def build_graph_with_constraints(
        self,
        num_total_nodes: int,
        min_calls: int,
        max_critical_path_length: int,
        num_disconnected_nodes: int = 0
    ) -> None:
        # Build the entire graph based on specified constraints.
        if min_calls < max_critical_path_length + 1:
            raise ValueError(f"`min_calls` must be >= `max_critical_path_length` + 1")
        if num_total_nodes < min_calls + num_disconnected_nodes:
            raise ValueError("`num_total_nodes` is too small for all constraints.")
        
        self.total_nodes = num_total_nodes
        self.min_calls = min_calls
        self.max_critical_path = max_critical_path_length
        self.disconnected_nodes = num_disconnected_nodes

        self.used_names.clear()
        self.graph = nx.DiGraph()
        self.desired_output_variable = None
        self.has_been_reformatted = False
        
        target_node_name = self._build_core_graph(min_calls, max_critical_path_length)
        if target_node_name:
            self.desired_target_node = target_node_name
            self.desired_output_variable = self.graph.nodes[target_node_name]['function'].output

        self._add_disconnected_nodes(num_disconnected_nodes)

        num_extra_connected = num_total_nodes - len(self.graph.nodes)
        if num_extra_connected > 0:
            self._add_extra_connected_nodes(num_extra_connected, target_node_name, max_critical_path_length)

        self._populate_function_descriptions()

    def _add_constrained_edge(self, producer: str, consumer: str, target_node: str, max_length: int) -> bool:
        """
        Adds an edge from producer to consumer only if it satisfies all graph constraints.
        """
        if producer == consumer or self.graph.has_edge(producer, consumer):
            return False
        if self._would_create_cycle(producer, consumer):
            return False
        self.graph.add_edge(producer, consumer)
        path_len = self._get_longest_path_length_to_target(self.graph, target_node)
        if path_len > max_length:
            self.graph.remove_edge(producer, consumer)
            return False
        else:
            producer_output = self.graph.nodes[producer]['function'].output
            consumer_func = self.graph.nodes[consumer]['function']
            if producer_output not in consumer_func.inputs:
                consumer_func.inputs.append(producer_output)
            return True

    def _get_longest_path_length_to_target(self, graph: nx.DiGraph, target_node: str) -> int:
        try:
            ancestor_graph = graph.subgraph(nx.ancestors(graph, target_node) | {target_node})
        except nx.NetworkXError:
            return 0
        if not nx.is_directed_acyclic_graph(ancestor_graph): return float('inf')
        dist = {n: 0 for n in ancestor_graph.nodes()}
        for n in nx.topological_sort(ancestor_graph):
            for succ in graph.successors(n):
                if succ in dist:
                    dist[succ] = max(dist[succ], dist[n] + 1)
        return max(dist.values())

    def _build_core_graph(self, min_calls: int, max_critical_path_length: int) -> Optional[str]:
        if min_calls == 0:
            return None
        first_node = self._create_node(inputs=[self._generate_random_name()], prefix="func", node_type='core')
        last_node_in_chain = first_node
        guaranteed_core_nodes = {first_node}
        if max_critical_path_length > 0:
            prev_output = self.graph.nodes[first_node]['function'].output
            for _ in range(max_critical_path_length):
                node_name = self._create_node(inputs=[prev_output], prefix="func", node_type='core')
                self.graph.add_edge(last_node_in_chain, node_name)
                guaranteed_core_nodes.add(node_name)
                last_node_in_chain = node_name
                prev_output = self.graph.nodes[node_name]['function'].output
        target_node = last_node_in_chain
        confirmed_side_nodes = set()
        num_side_nodes_required = min_calls - len(guaranteed_core_nodes)
        if num_side_nodes_required < 0:
            num_side_nodes_required = 0
        candidate_pool = [self._create_node(inputs=[self._generate_random_name()], prefix="func", node_type='core') for _ in range(num_side_nodes_required)]
        while len(confirmed_side_nodes) < num_side_nodes_required:
            if not candidate_pool:
                candidate = self._create_node(inputs=[self._generate_random_name()], prefix="func", node_type='core')
            else:
                candidate = candidate_pool.pop(0)
            possible_consumers = list(guaranteed_core_nodes | confirmed_side_nodes)
            random.shuffle(possible_consumers)
            has_connected = False
            for consumer in possible_consumers:
                if self._add_constrained_edge(candidate, consumer, target_node, max_critical_path_length):
                    has_connected = True
                    break
            if has_connected:
                confirmed_side_nodes.add(candidate)
            else:
                func_obj = self.graph.nodes[candidate]['function']
                self.graph.remove_node(candidate)
                if func_obj.name in self.used_names: self.used_names.remove(func_obj.name)
                if func_obj.output in self.used_names: self.used_names.remove(func_obj.output)
        all_core_nodes = list(guaranteed_core_nodes | confirmed_side_nodes)
        num_attempts = int(len(all_core_nodes) * 2.0)
        for _ in range(num_attempts):
            producer = random.choice(all_core_nodes)
            consumer = random.choice(all_core_nodes)
            self._add_constrained_edge(producer, consumer, target_node, max_critical_path_length)
        return target_node

    def get_metrics(self, print_metrics=True):
        graph = self.graph
        if not nx.is_directed_acyclic_graph(graph):
            raise ValueError("Graph must be a DAG")
        if len(graph) == 0:
            return {"error": "Empty graph"}
        metrics = {}
        n_nodes = graph.number_of_nodes()
        n_edges = graph.number_of_edges()
        sources = [n for n in graph.nodes if graph.in_degree(n) == 0]
        sinks = [n for n in graph.nodes if graph.out_degree(n) == 0]
        metrics['num_nodes'] = n_nodes
        metrics['num_edges'] = n_edges
        metrics['num_variables'] = self.get_number_of_variables()
        metrics['num_sources'] = len(sources)
        metrics['num_sinks'] = len(sinks)
        metrics['num_input_vars'] = len(self.get_input_vars())
        degrees = [d for _, d in graph.degree()]
        metrics['average_degree'] = np.mean(degrees) if degrees else 0
        if print_metrics:
            print("\n=== Function Graph Metrics ===")
            for k, v in metrics.items():
                print(f"\t{k}: {v:.3f}" if isinstance(v, float) else f"\t{k}: {v}")
        return metrics

    def get_number_of_variables(self):
        return len(self.used_names)

    def get_input_vars(self) -> List[str]:
        all_inputs = set()
        all_outputs = set()
        for _, data in self.graph.nodes(data=True):
            func = data['function']
            all_inputs.update(func.inputs)
            all_outputs.add(func.output)
        input_vars = list(all_inputs - all_outputs)
        return input_vars

    def check_critical_path_constraint(self, max_length: int) -> bool:
        print("\n--- Validating Critical Path Length ---")
        if not self.desired_output_variable:
            print("Warning: No target node set. Cannot check critical path length.")
            return True
        target_node = None
        for n, data in self.graph.nodes(data=True):
            if data['function'].output == self.desired_output_variable:
                target_node = n
                break
        if not target_node:
            print(f"Error: Target output variable '{self.desired_output_variable}' does not correspond to any node.")
            return False
        actual_max_length = self._get_longest_path_length_to_target(self.graph, target_node)
        if actual_max_length > max_length:
            print(f"CONSTRAINT VIOLATED: Max critical path length is {actual_max_length}, but should be <= {max_length}.")
            return False
        else:
            print(f"CONSTRAINT SATISFIED: Max critical path length is {actual_max_length} (<= {max_length}).")
            return True

    def _add_disconnected_nodes(self, num_nodes: int):
        if num_nodes == 0: return
        disconnected_node_names = [self._create_node(inputs=[self._generate_random_name("")], prefix="func", node_type='disconnected') for _ in range(num_nodes)]
        num_edges_to_add = num_nodes // 2
        for _ in range(num_edges_to_add):
            parent = random.choice(disconnected_node_names)
            child = random.choice(disconnected_node_names)
            if parent != child and not self.graph.has_edge(parent, child) and not self._would_create_cycle(parent, child):
                parent_output = self.graph.nodes[parent]['function'].output
                child_func = self.graph.nodes[child]['function']
                if child_func.inputs:
                    child_func.inputs.pop(random.randrange(len(child_func.inputs)))
                    child_func.inputs.append(parent_output)
                    self.graph.add_edge(parent, child)

    def _add_extra_connected_nodes(
        self, num_nodes: int, target_node: Optional[str], max_length: int
    ):
        core_nodes = [n for n, data in self.graph.nodes(data=True) if data.get('node_type') in ('core', 'extra')]
        # if not core_nodes:
        #     self._add_disconnected_nodes(num_nodes); return
        for _ in range(num_nodes):
            producer_node = random.choice(core_nodes)
            new_node = self._create_node(inputs=[], prefix="func", node_type='extra')
            if not target_node or self._add_constrained_edge(producer_node, new_node, target_node, max_length):
                core_nodes.append(new_node)

    def _populate_function_descriptions(self):
        for node in self.graph.nodes:
            func = self.graph.nodes[node]['function']
            input_str = ", ".join(func.inputs) if func.inputs else "no inputs"
            func.description = f"This function '{func.name}' takes {input_str} and produces '{func.output}'."

    def _would_create_cycle(self, src: str, dst: str) -> bool:
        self.graph.add_edge(src, dst)
        has_cycle = not nx.is_directed_acyclic_graph(self.graph)
        self.graph.remove_edge(src, dst)
        return has_cycle

    def to_python_code(self) -> str:
        if not nx.is_directed_acyclic_graph(self.graph):
            raise ValueError("Cannot generate code from a graph with cycles.")
        code_lines = ["import random", "import time", "\n# --- Function Definitions ---"]
        for node_name in self.graph.nodes:
            func = self.graph.nodes[node_name]['function']
            signature = f"def {func.name}({', '.join(func.inputs)}):"
            docstring = f'    """\n    {func.description}\n    """'
            body_logic = f"hash(f\"{''.join(func.inputs)}{func.output}\")"
            if func.inputs:
                input_logic = " + ".join([f"abs(hash(str({v})))" for v in func.inputs])
                body_logic += " + " + input_logic
            body = [
                f'    # print(f"Executing {func.name}...")',
                "    time.sleep(random.uniform(0.01, 0.02)) # Simulate work",
                f"    {func.output} = ({body_logic}) % 10000",
                f"    return {func.output}"
            ]
            code_lines.append("\n" + signature)
            code_lines.append(docstring)
            code_lines.extend(body)
        code_lines.append("\n\n# --- Main Execution Logic ---")
        code_lines.append("if __name__ == '__main__':")
        input_vars = self.get_input_vars()
        code_lines.append("    # Initialize input variables")
        for var in input_vars:
            code_lines.append(f"    {var} = random.randint(100, 200)")
        code_lines.append("    print('--- Initializing Inputs ---')")
        code_lines.append("    print({ " + ", ".join([f"f'\\\"{v}\\\":{{{v}}}'" for v in input_vars]) + " })")
        code_lines.append("    print('--- Starting Execution ---\\n')")
        exec_graph = self.graph
        if self.desired_output_variable:
            target_node = next((n for n, d in self.graph.nodes(data=True) if d['function'].output == self.desired_output_variable), None)
            if target_node:
                ancestors = nx.ancestors(self.graph, target_node)
                ancestors.add(target_node)
                exec_graph = self.graph.subgraph(ancestors)
        for node_name in nx.topological_sort(exec_graph):
            func = self.graph.nodes[node_name]['function']
            inputs_str = ", ".join(func.inputs)
            call_str = f"    {func.output} = {func.name}({inputs_str})"
            code_lines.append(call_str)
        if self.desired_output_variable:
            code_lines.append("\n    # --- Final Result ---")
            code_lines.append(f"    print(f'\\nFinal desired output \\'{self.desired_output_variable}\\' has value: {{{self.desired_output_variable}}}')")
        else:
            code_lines.append("\n    print('\\nExecution finished. No specific output was desired.')")
        return "\n".join(code_lines)

    def reformat_tree_with_shared_types(self, show_variable_names_in_description=True, rename_variables=False, use_subtypes=False, num_supertypes=3):
        """
        Replace variable names in the graph so that data dependencies are indicated only by shared types,
        not shared variable names. All variable names become unique, but types are shared where needed.
        """
        if self.has_been_reformatted:
            raise Exception("Can't call reformat_tree_with_shared_types multiple times on the same tree.")
        self.has_been_reformatted = True

        variable_to_type = {}
        type_to_varnames = defaultdict(list)

        # Step 1: assign a type to every unique variable name in the graph (inputs + outputs)
        if not use_subtypes:
            for node in self.graph.nodes:
                func = self.graph.nodes[node]['function']
                for var in func.inputs + [func.output]:
                    if var not in variable_to_type:
                        variable_to_type[var] = self._generate_random_name("type")
        else:
            supertypes = [self._generate_random_name("type") for _ in range(num_supertypes)]
            for node in self.graph.nodes:
                func = self.graph.nodes[node]['function']
                for var in func.inputs + [func.output]:
                    if var not in variable_to_type:
                        np.random.choice(supertypes)
                        variable_to_type[var] = np.random.choice(supertypes) + " with " + self._generate_random_name("subtype")

        # Step 2: assign new *unique* variable names, re-linking them via type
        old_to_new_name = {}
        new_variable_to_type = {}

        for node in self.graph.nodes:
            func = self.graph.nodes[node]['function']

            new_inputs = []
            for old_input in func.inputs:
                type_id = variable_to_type[old_input]
                new_input = self._generate_random_name()
                old_to_new_name[old_input] = new_input
                new_variable_to_type[new_input] = type_id
                type_to_varnames[type_id].append((func.name, new_input))
                new_inputs.append(new_input)

            old_output = func.output
            type_id = variable_to_type[old_output]
            new_output = self._generate_random_name()
            old_to_new_name[old_output] = new_output
            new_variable_to_type[new_output] = type_id
            type_to_varnames[type_id].append((func.name, new_output))

            func.inputs = new_inputs
            func.output = new_output

        # Save the generated maps as instance attributes.
        self.variable_to_type_map = new_variable_to_type
        self.old_to_new_name_map = old_to_new_name  # <<< THIS LINE IS ADDED

        # Step 3: update function descriptions using new names and shared types
        for node in self.graph.nodes:
            func = self.graph.nodes[node]['function']
            if rename_variables:
                for i, var in enumerate(func.inputs):
                    new_name = f"{func.name}_input_{i}"
                    new_variable_to_type[new_name] = new_variable_to_type[var]
                    func.inputs[i] = new_name

                output_new_name = f"{func.name}_output"
                new_variable_to_type[output_new_name] = new_variable_to_type[func.output]
                func.output = output_new_name

            if show_variable_names_in_description:
                input_descs = [f"{var} ({self.variable_to_type_map[var]})" for var in func.inputs]
                output_desc = f"{func.output} ({self.variable_to_type_map[func.output]})"
                if len(func.inputs) == 1:
                    func.description = f"Processes variable {input_descs[0]} to produce variable {output_desc}"
                else:
                    func.description = f"Processes variables {', '.join(input_descs)} to produce variable {output_desc}"
            else:
                input_descs = [f"({self.variable_to_type_map[var]})" for var in func.inputs]
                output_desc = f"({self.variable_to_type_map[func.output]})"
                if len(func.inputs) == 1:
                    func.description = f"Processes inputs of types {input_descs[0]} to produce {output_desc}"
                else:
                    func.description = f"Processes inputs of types {', '.join(input_descs)} to produce {output_desc}"

