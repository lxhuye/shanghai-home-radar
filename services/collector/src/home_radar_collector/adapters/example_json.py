from home_radar_collector.adapters.canonical_json import CanonicalJsonFeedAdapter

# Compatibility import for existing operators. New code should use the canonical name.
ExampleJsonFeedAdapter = CanonicalJsonFeedAdapter

__all__ = ["ExampleJsonFeedAdapter"]
