"""REST API. The r-App drives this; it is the run lifecycle boundary.

    POST /experiment/start  {"label": "...", "meta": {...}}
    POST /experiment/stop
    GET  /experiment/status
    GET  /healthz
    GET  /snapshot          one immediate delta, no run required

Flask is optional: `once` mode needs no HTTP at all.
"""
import time


def serve(agent, cfg):
    try:
        from flask import Flask, jsonify, request
    except ImportError:
        print("flask not installed: pip install 'ocloud-telemetry-agent[api]' "
              "or use `once` mode", flush=True)
        return 2

    app = Flask(__name__)

    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True, ts=time.time())

    @app.get("/experiment/status")
    def status():
        return jsonify(agent.status())

    @app.post("/experiment/start")
    def start():
        body = request.get_json(silent=True) or {}
        label = body.get("label") or "run"
        try:
            return jsonify(agent.start(label, body.get("meta") or {})), 201
        except RuntimeError as e:
            return jsonify(error=str(e)), 409

    @app.post("/experiment/stop")
    def stop():
        try:
            return jsonify(agent.stop())
        except RuntimeError as e:
            return jsonify(error=str(e)), 409

    @app.get("/snapshot")
    def snapshot():
        from ..core.sampler import Sampler
        out = {}
        s = Sampler(agent.collectors, 1.0, lambda x: out.update(x))
        s.start()
        s.join(timeout=6)
        s.stop()
        return jsonify(out or {"error": "no sample produced"})

    print("serving on %s:%d" % (cfg.api_host, cfg.api_port), flush=True)
    app.run(host=cfg.api_host, port=cfg.api_port, threaded=True)
    return 0
