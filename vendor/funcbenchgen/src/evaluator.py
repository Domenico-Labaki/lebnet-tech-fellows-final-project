import random
import json
from collections import defaultdict
import re

class ToolCallingEvaluator:
    # error_detail_level can be "None", "Basic", or "Advanced"
    # on_wrong_inputs can be Execute, Error, or End
    def __init__(self, function_tree, seed=None, error_detail_level="Basic", on_wrong_inputs="Error", num_noise_inputs: int = 0, function_shuffle_seed: int = 42, repeat_known_variable_values=True):
        self.function_tree = function_tree
        self.error_detail_level = error_detail_level
        self.on_wrong_inputs = on_wrong_inputs
        self.num_noise_inputs = num_noise_inputs
        self.function_shuffle_seed = function_shuffle_seed
        self.revealed_vars = {}  # Track specifically revealed variables and their known values
        self.repeat_known_variable_values = repeat_known_variable_values
        if seed is None:
            seed = random.choice(range(100000))
        random.seed(seed)

    def _get_function(self, func_name):
        return self.function_tree.graph.nodes[func_name]['function']
    
    def _split_input_vars_by_core(self, input_vars):
        """
        Split graph-level input variables into two sets:
        - core_inputs: inputs that feed at least one 'core' node
        - non_core_inputs: inputs that do not feed any 'core' node
        If an input variable is not matched to any node's inputs, treat it as non-core.
        """
        core_inputs = set()
        non_core_inputs = set()
        g = self.function_tree.graph

        for node, data in g.nodes(data=True):
            func = data.get('function')
            node_type = data.get('node_type')
            if not func:
                continue
            for v in getattr(func, 'inputs', []):
                if v in input_vars:
                    if node_type == 'core':
                        core_inputs.add(v)
                    else:
                        non_core_inputs.add(v)

        unmatched = set(input_vars) - core_inputs - non_core_inputs
        non_core_inputs |= unmatched
        return sorted(core_inputs), sorted(non_core_inputs)

    def _get_input_prompt(self, desired_output_variable, input_vars):
        """
        Build the user prompt that discloses input variables.
        - Always include inputs used by 'core' nodes.
        - From non-core inputs, include a random subset of size `self.num_noise_inputs`.
        - If `self.num_noise_inputs == 0`, include core-only (default).
        """
        include_vars = list(input_vars)
        if self.num_noise_inputs > 0:
            core_inputs, non_core_inputs = self._split_input_vars_by_core(input_vars)
            k = max(0, min(self.num_noise_inputs, len(non_core_inputs)))
            noise_samples = random.sample(non_core_inputs, k) if k > 0 else []
            include_vars = sorted(set(core_inputs) | set(noise_samples))
        else:
            # Only core inputs when num_noise_inputs == 0
            core_inputs, _ = self._split_input_vars_by_core(input_vars)
            include_vars = core_inputs

        # Initialize revealed_vars with variables shown in the prompt
        self.revealed_vars = {var: self.variable_values[var] for var in include_vars}

        prompt = (
            f"Using the tools at your disposal, use function(s) to compute and give me the correct "
            f"value of variable {desired_output_variable}.\n"
        )
        for inp in include_vars:
            # Assumes evaluator.variable_values holds values for all input vars
            prompt += f"Variable {inp} = {self.variable_values[inp]}\n"

        prompt += "You have all the information you need to get the correct result."
        return prompt
    
    # def _get_input_prompt(self, desired_output_variable, input_vars):
    #     prompt = f"Using the tools at your disposal, use functions until you are able to give me the correct value of variable {desired_output_variable}.\n"
    #     for inp in input_vars:
    #         prompt += f"Variable {inp} = {self.variable_values[inp]}\n"

    #     prompt += """You have all the information you need to get the correct result."""

    #     return prompt

    def _get_function_schemas(self):
        graph = self.function_tree.graph
        signatures = []
        nodes = list(graph.nodes())

        if self.function_shuffle_seed is not None:
            rnd = random.Random(self.function_shuffle_seed) 
            rnd.shuffle(nodes)
        else:
            pass

        for node in nodes:
            func = self._get_function(node)
            signatures.append({
                "type": "function",
                "function": {
                    "name": node,
                    "description": func.description,
                    "strict": True,
                    "parameters": {
                        "type": "object",
                        "properties": {inp: {"type": "integer"} for inp in func.inputs},
                        "required": func.inputs,
                        "additionalProperties": False,
                    }
                }
            })
        return signatures

    def _parse_types_from_descriptions(self):
        var_to_type = {}
        type_to_vars = defaultdict(list)
        pattern_without_names = re.compile(r"\(type: ([^)]+)\)")
        for node in self.function_tree.graph.nodes:
            func = self._get_function(node)
            types = pattern_without_names.findall(func.description)
            vars_in_order = func.inputs + [func.output]
            for var, var_type in zip(vars_in_order, types):
                var_to_type[var] = var_type
                type_to_vars[var_type].append(var)
        return var_to_type, type_to_vars
    
    def _get_input_variables_with_types(self, var_to_type, type_to_vars):
        known_types = set()
        produced_types = set()
        consumed_types = set()
        for node in self.function_tree.graph.nodes:
            func = self._get_function(node)
            produced_types.add(var_to_type[func.output])
            for inp in func.inputs:
                consumed_types.add(var_to_type[inp])
        input_types = consumed_types - produced_types

        input_vars = []
        for t, vars in type_to_vars.items():
            if t in input_types:
                input_vars.append(vars[0])
                known_types.add(t)

        return input_vars, known_types

    def _format_error(self, detailed_msg, short_msg):
        if self.error_detail_level == "None":
            return "Error occurred.", short_msg
        if self.error_detail_level == "Basic" and short_msg in {
            "Variable value not yet known",
            "Value of variable was incorrect"
        }:
            return "Error occurred.", short_msg
        return f"Error: {detailed_msg}", short_msg
    
    def _known_variables_to_string(self, known, use_types, var_to_type):
        result = "\nAs a reminder, here are the variable values that are currently known."
        if use_types:
            for var, known_value in self.revealed_vars.items():
                if var_to_type and var in var_to_type:
                    type_name = var_to_type[var]
                    result += f"\nVariable {var} ({type_name}) = {known_value}"
        else:
            for var, known_value in self.revealed_vars.items():
                result += f"\nVariable {var} = {known_value}"

        return result

    def _execute_tool(self, tool_call, known, use_types=False, var_to_type=None):
        func_name = tool_call.function.name

        # Check if the function exists in the graph
        if func_name not in self.function_tree.graph.nodes:
            return self._format_error(
                f"No function called {func_name} in graph.",
                "Func not in graph"
            )
        
        function = self._get_function(func_name)

        function_call_inputs = json.loads(tool_call.function.arguments)

        # Check for missing or extra inputs
        for required_input in function.inputs:
            if required_input not in function_call_inputs:
                return self._format_error(
                    f"Function {func_name} missing required input {required_input}.",
                    "Gave too many inputs for function"
                )
            
        for input_name in function_call_inputs:
            if input_name not in function.inputs:
                return self._format_error(
                    f"Function {func_name} received unexpected input {input_name}.",
                    "Did not give all inputs for function"
                )

            variable_known = False
            if use_types:
                input_type = var_to_type.get(input_name)
                if input_type is None:
                    variable_known = False
                else:
                    variable_known = input_type in known
            else:
                variable_known = input_name in known
            if not variable_known:
                if self.on_wrong_inputs == "Error":
                    return self._format_error(
                        f"Type of variable {input_name} is not yet known.",
                        "Variable value not yet known"
                    )
                else:
                    while True:
                        return_value = random.choice(range(1000))
                        if return_value != self.variable_values[function.output]:
                            break
                    result = f"Variable {function.output} = {return_value}."
                    if self.repeat_known_variable_values:
                        result += self._known_variables_to_string(known, use_types, var_to_type)
                    # Even when a function is called wrong, we need to keep track of the wrong value returned
                    self.revealed_vars[function.output] = return_value

                    return result, "Variable value not yet known"                  

            # Check correctness
            if function_call_inputs[input_name] != self.variable_values[input_name]:
                if self.on_wrong_inputs == "Error":
                    return self._format_error(
                        f"The value of variable {input_name} is incorrect.",
                        "Value of variable was incorrect"
                    )
                else:
                    while True:
                        return_value = random.choice(range(1000))
                        if return_value != self.variable_values[function.output]:
                            break
                    result = f"Variable {function.output} = {return_value}."
                    if self.repeat_known_variable_values:
                        result += self._known_variables_to_string(known, use_types, var_to_type)
                    self.revealed_vars[function.output] = return_value

                    return result, "Value of variable was incorrect"

        result = f"Variable {function.output} = {self.variable_values[function.output]}."

        if self.repeat_known_variable_values:
            result += self._known_variables_to_string(known, use_types, var_to_type)
        self.revealed_vars[function.output] = self.variable_values[function.output]

        # Function call is valid
        if use_types:
            known.add(var_to_type[function.output])
        else:
            known.add(function.output)    

        return result, None
