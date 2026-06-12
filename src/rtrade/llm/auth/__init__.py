"""Pluggable credential providers for LLM calls.

Setiap provider menjawab satu pertanyaan: "untuk model ini, bagaimana cara
mengautentikasi panggilan litellm?" — via API key, bearer token OAuth, atau
kredensial Vertex/Azure. Runtime tidak tahu detailnya, hanya memanggil resolve().
"""
