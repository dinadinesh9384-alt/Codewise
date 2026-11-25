"""Agent 5 runner for bounded inventory and gateway-backed migration planning."""

from .services.analysis_service import analyze_repository


class MigrationPlanner:
    def __init__(self, repository_reader):
        self.repository_reader = repository_reader

    def run(self, validated_input, execution_context, chat_model):
        bounded_model = chat_model.with_max_output_tokens(validated_input.options.max_output_tokens)
        return analyze_repository(validated_input, execution_context, self.repository_reader, bounded_model)
