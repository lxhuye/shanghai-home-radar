FROM shanghai-home-radar-api:latest
USER root
RUN mkdir -p /state /exports && chown radar:radar /state /exports && chmod 700 /state
USER radar
WORKDIR /app
COPY --chmod=644 scripts/browser_handoff.py scripts/browser_handoff.html scripts/prepare_public_monitor_pilot.py scripts/build_calibration_set.py /app/scripts/
COPY --chmod=644 scripts/antibot_detection.py /app/scripts/
COPY --chmod=644 scripts/recommendation_routes.py scripts/recommendation_evidence.py scripts/recommendation_future.py scripts/recommendation_market.py scripts/buyer_profile.py scripts/browser_validation.py /app/scripts/
COPY --chmod=644 scripts/research_pipeline.py scripts/research_evidence.py scripts/import_future_evidence.py scripts/geocode_listings.py /app/scripts/
COPY --chmod=644 data/evidence/sample/transport_stations.osm.yaml data/evidence/sample/district_factors.yearbook_2022_2024.yaml data/evidence/sample/browser_geocodes.json /app/data/evidence/sample/
COPY --chmod=644 config/validation.yaml config/market_baseline.yaml config/valuation.yaml config/forecasting.yaml config/decision.yaml /app/config/
ENV PYTHONPATH=/app
CMD ["python", "-m", "scripts.browser_handoff"]
