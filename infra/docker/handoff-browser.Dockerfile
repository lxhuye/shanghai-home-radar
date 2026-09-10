FROM selenium/standalone-chromium:4.48.0-20260909@sha256:3e2bffbae77f2d56014200bc4ef2eed48308e5b4cb3fdc2ff5edfa8c1b574170
USER root
RUN mkdir -p /home/seluser/homeradar-profile && chown 1200:1201 /home/seluser/homeradar-profile && chmod 700 /home/seluser/homeradar-profile
USER 1200:1201
