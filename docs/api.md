# API reference

Auto-generated reference for the public Python surface. Hidden modules
(`*._private`) are intentionally not rendered.

## `bedrock_strands_agent.api`

::: bedrock_strands_agent.api.app
    options:
      members:
        - create_app

::: bedrock_strands_agent.api.auth
    options:
      members:
        - AuthMiddleware
        - AuthError
        - JWKSCache

::: bedrock_strands_agent.api.ratelimit
    options:
      members:
        - build_limiter
        - rate_limit_string

## `bedrock_strands_agent.extraction`

::: bedrock_strands_agent.extraction.service
    options:
      members:
        - ExtractionService
        - ExtractionError

::: bedrock_strands_agent.extraction.models
    options:
      members:
        - FormSchema
        - FieldDefinition
        - FieldType
        - ExtractedField
        - ExtractionRequest
        - ExtractionResult

## `bedrock_strands_agent.agent`

::: bedrock_strands_agent.agent.builder
    options:
      members:
        - build_agent
        - AgentBundle

::: bedrock_strands_agent.agent.bedrock_retry
    options:
      members:
        - invoke_with_retry

## `bedrock_strands_agent.config`

::: bedrock_strands_agent.config
    options:
      members:
        - Settings
        - get_settings
        - reset_settings_cache
