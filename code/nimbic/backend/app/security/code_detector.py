import re
from dataclasses import dataclass
from typing import List, Literal


@dataclass
class CodeSnippet:
    language: str
    snippet: str
    start_line: int
    indicator_type: str  # "function_def" | "import" | "sql_query" | "secret_pattern" | "shebang"


@dataclass
class CodeResult:
    has_code: bool
    languages: List[str]
    snippets: List[CodeSnippet]
    severity: Literal["low", "medium", "high"]


class CodeDetector:
    """
    Heuristics-based source code detector for outbound LLM prompts.
    Scans for programming languages, configurations, and key credentials.
    """

    async def analyze(self, text: str) -> CodeResult:
        if not text:
            return CodeResult(has_code=False, languages=[], snippets=[], severity="low")

        lines = text.splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            return CodeResult(has_code=False, languages=[], snippets=[], severity="low")

        # Signature definitions mapping to (language, indicator_type)
        signatures = {
            # Shebang / Shell script indicators
            r"#!/bin/bash": ("shell", "shebang"),
            r"#!/bin/sh": ("shell", "shebang"),
            r"#!/usr/bin/env python": ("python", "shebang"),
            r"^#!\s*/\w+/": ("shell", "shebang"),
            
            # Private keys / credentials
            r"-----BEGIN": ("private_key", "secret_pattern"),
            r"ssh-rsa AAAA": ("private_key", "secret_pattern"),
            
            # Python indicators
            r"\bdef\s+\w+\s*\(": ("python", "function_def"),
            r"\bimport\s+[\w\s,]+": ("python", "import"),
            r"\bfrom\s+[\w\.]+\s+import\b": ("python", "import"),
            r"\bclass\s+\w+\s*[:(]": ("python", "function_def"),
            r"if\s+__name__\s*==\s*['\"]__main__['\"]": ("python", "function_def"),
            
            # JS/TS indicators
            r"\bfunction\s*\w*\s*\(": ("javascript", "function_def"),
            r"\(.*?\)\s*=>\s*\{": ("javascript", "function_def"),
            r"\brequire\s*\(\s*['\"]": ("javascript", "import"),
            r"\bimport\s+.*?\s+from\s+['\"]": ("javascript", "import"),
            r"\bmodule\.exports\s*=": ("javascript", "import"),
            
            # SQL indicators
            r"\bSELECT\s+.*?\s+FROM\b": ("sql", "sql_query"),
            r"\bINSERT\s+INTO\b": ("sql", "sql_query"),
            r"\bUPDATE\s+.*?\s+SET\b": ("sql", "sql_query"),
            r"\bDROP\s+TABLE\b": ("sql", "sql_query"),
            r"\bCREATE\s+TABLE\b": ("sql", "sql_query"),
            r"\bDELETE\s+FROM\b": ("sql", "sql_query"),
            r"\bTRUNCATE\s+TABLE\b": ("sql", "sql_query"),
            
            # Java/Kotlin indicators
            r"\bpublic\s+class\s+\w+": ("java", "function_def"),
            r"\bimport\s+java\.\w+\.": ("java", "import"),
            r"@Override\b": ("java", "function_def"),
            r"\bfun\s+\w+\s*\(": ("kotlin", "function_def"),
            
            # Shell syntax
            r"\bchmod\s+[+-x\d]+": ("shell", "function_def"),
            r"\bsudo\s+\w+": ("shell", "function_def"),
            r"\becho\s+['\"]": ("shell", "function_def"),
            
            # Kubernetes/YAML/JSON config indicators
            r"^apiVersion:": ("yaml", "import"),
            r"^kind:\s*(Deployment|Pod|Service|ConfigMap)\b": ("yaml", "import"),
            r"\"version\"\s*:\s*\"": ("json", "import"),
            
            # Dockerfile
            r"^FROM\s+[\w\.:/-]+": ("dockerfile", "import"),
            r"^RUN\s+": ("dockerfile", "import"),
            r"^COPY\s+": ("dockerfile", "import"),
            r"^EXPOSE\s+\d+": ("dockerfile", "import"),
            
            # Terraform
            r"^resource\s+\"\w+\"\s+\"\w+\"\s*\{": ("terraform", "import"),
            r"^provider\s+\"\w+\"\s*\{": ("terraform", "import"),
            r"^terraform\s*\{": ("terraform", "import")
        }

        # Track indices and info of matching lines
        matching_lines_count = 0
        snippets: List[CodeSnippet] = []
        detected_languages = set()
        
        has_dangerous_sql = False

        for idx, line in enumerate(lines):
            line_str = line.strip()
            if not line_str:
                continue
                
            matched = False
            for pattern, (lang, indicator) in signatures.items():
                if re.search(pattern, line_str, re.IGNORECASE if lang != "yaml" and lang != "dockerfile" else 0):
                    matched = True
                    detected_languages.add(lang)
                    
                    # Capture snippet (limit to 100 characters)
                    clean_snippet = line_str[:100]
                    
                    # Prevent redundant snippets on the exact same line matching multiple things
                    if not any(s.start_line == idx + 1 for s in snippets):
                        snippets.append(CodeSnippet(
                            language=lang,
                            snippet=clean_snippet,
                            start_line=idx + 1,
                            indicator_type=indicator
                        ))
                    
                    # Watch for high-severity SQL commands
                    if lang == "sql" and re.search(r"\b(DROP|DELETE|TRUNCATE)\b", line_str, re.IGNORECASE):
                        has_dangerous_sql = True
                        
            if matched:
                matching_lines_count += 1

        # Scoring
        fraction = matching_lines_count / total_lines if total_lines > 0 else 0.0
        
        if fraction > 0.30:
            severity = "high"
        elif fraction >= 0.10:
            severity = "medium"
        else:
            severity = "low"
            
        # SQL drop/delete/truncate overrides to high severity
        if has_dangerous_sql:
            severity = "high"

        has_code = len(snippets) > 0

        # Remove "private_key" from languages list (it's not a programming language)
        languages_list = sorted(list(detected_languages - {"private_key"}))

        return CodeResult(
            has_code=has_code,
            languages=languages_list,
            snippets=snippets,
            severity=severity
        )
