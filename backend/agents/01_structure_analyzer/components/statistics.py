from collections import Counter, defaultdict
from pathlib import PurePosixPath

from ..schemas.repository import FileDistribution, LanguageStat, NodeType, RepositoryNode, SizeDistribution
from .file_classifier import classify_file, extension


def calculate_statistics(records, languages, *, max_directory_depth, evidence_for=None):
    nodes = {".": RepositoryNode(path=".", name=".", node_type=NodeType.ROOT)}
    directory_paths = set()
    skipped_depth = set()
    extension_counts, extension_bytes = Counter(), Counter()
    size_counts, size_bytes = Counter(), Counter()
    language_counts, language_bytes, language_methods, language_certainty = Counter(), Counter(), defaultdict(set), {}
    language_paths = defaultdict(list)
    deepest = 0
    for record in records:
        path = record["path"]
        parts = path.split("/")
        depth = len(parts) - 1
        deepest = max(deepest, depth)
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            directory_paths.add(parent)
            if index <= max_directory_depth:
                nodes.setdefault(parent, RepositoryNode(path=parent, name=parts[index-1], node_type=NodeType.DIRECTORY))
        size = record["size_bytes"]
        bucket = "0-1 KiB" if size <= 1024 else ("1-10 KiB" if size <= 10_240 else
            ("10-100 KiB" if size <= 102_400 else ("100 KiB-1 MiB" if size <= 1_048_576 else ">1 MiB")))
        size_counts[bucket] += 1
        size_bytes[bucket] += size
        if depth <= max_directory_depth:
            nodes[path] = RepositoryNode(path=path, name=parts[-1], node_type=NodeType.FILE, size_bytes=size,
                                         extension=extension(path))
        else:
            skipped_depth.add(path)
        ext = extension(path)
        extension_counts[ext] += 1
        extension_bytes[ext] += size
        language, method, certainty = languages.get(path, (*classify_file(path),))
        language_counts[language] += 1
        language_bytes[language] += size
        language_paths[language].append(path)
        if method != "none": language_methods[language].add(method)
        language_certainty[language] = "high" if certainty == "high" else (language_certainty.get(language, "unknown"))
    tree = tuple(nodes[path] for path in sorted(nodes, key=lambda path: (path.count("/"), path)))
    lang_stats = tuple(LanguageStat(language=lang, file_count=language_counts[lang], bytes=language_bytes[lang],
        detection_methods=tuple(sorted(language_methods[lang])), certainty=language_certainty.get(lang, "unknown"),
        evidence=tuple(evidence_for(path, "language_detector") for path in language_paths[lang][:5]) if evidence_for else ())
        for lang in sorted(language_counts))
    distributions = tuple(FileDistribution(extension=ext, file_count=extension_counts[ext], bytes=extension_bytes[ext])
                          for ext in sorted(extension_counts))
    size_distributions = tuple(SizeDistribution(bucket=bucket, file_count=size_counts[bucket], bytes=size_bytes[bucket])
                               for bucket in ("0-1 KiB", "1-10 KiB", "10-100 KiB", "100 KiB-1 MiB", ">1 MiB") if bucket in size_counts)
    return tree, len(directory_paths), deepest, lang_stats, distributions, tuple(sorted(skipped_depth)), size_distributions
