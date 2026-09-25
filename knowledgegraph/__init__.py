"""knowledgegraph - turn a folder of code and docs into a queryable knowledge graph.

Pipeline: detect -> extract -> build -> cluster -> analyze -> report -> query.
The agents package (LangGraph multi-agent system) answers user questions on
top of the graph.
"""
from knowledgegraph import ids, paths, security  # noqa: F401

__version__ = "0.1.0"
