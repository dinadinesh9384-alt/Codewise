"""Agent 8 runner. It reviews a sealed snapshot but never changes or executes it."""

from .services.review_service import review_analysis


class ReviewAgent:
    def __init__(self, repository_reader):
        self.repository_reader = repository_reader

    def run(self, validated_input, execution_context, chat_model):
        model = chat_model.with_max_output_tokens(4000)
        return review_analysis(validated_input, execution_context, self.repository_reader, model)
