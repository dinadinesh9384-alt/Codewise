"""Agent invocation adapter; it performs only deterministic snapshot analysis."""

from .services.analysis_service import analyze_repository


class StructureAnalyzer:
    def __init__(self, repository_reader):
        self.repository_reader = repository_reader

    def run(self, validated_input, execution_context):
        return analyze_repository(validated_input, execution_context, self.repository_reader)
