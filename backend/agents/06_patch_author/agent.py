"""Agent 6 runner: proposes and validates diffs without applying them."""

from .services.analysis_service import author_patches


class PatchAuthor:
    def __init__(self, repository_reader):
        self.repository_reader = repository_reader

    def run(self, validated_input, execution_context, chat_model):
        bounded_model = chat_model.with_max_output_tokens(validated_input.options.max_output_tokens)
        return author_patches(validated_input, execution_context, self.repository_reader, bounded_model)
