import threading
import json
import os
import signal
import sys
import atexit
from confluent_kafka import Consumer, Producer
from nicegui import ui, app
from sigmf_plotter import SigMFPanoramaPlotter
import subprocess
import plotly.graph_objects as go

# Optional dechirp demo utilities (included in this drop-in)
try:
    from dechirp_tools import (
        make_dechirp_waterfall,
        make_shifted_waterfall,
        load_sigmf_meta,
        parse_sigmf_params,
    )
except Exception:
    make_dechirp_waterfall = None
    make_shifted_waterfall = None
    load_sigmf_meta = None
    parse_sigmf_params = None

# Optional thin NGVA adapter (JSON over udp/http/stdout). File is included in the drop-in.
try:
    import ngva_adapter
except Exception:
    ngva_adapter = None
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class KafkaSettings(BaseSettings):
    bootstrap_servers: str = Field("kafka:29092", alias="KAFKA_BOOTSTRAP_SERVERS")
    group_id: str = Field("local-analysis-group", alias="GROUP_ID")
    topic: str = Field("iq_stream_topic", alias="KAFKA_TOPIC")
    auto_offset_reset: str = "earliest"

    # Optional: run detector + publish results
    detections_topic: str = Field("iq_detections_topic", alias="KAFKA_DETECTIONS_TOPIC")
    enable_detector: bool = Field(False, alias="ENABLE_DETECTOR")
    detector_cmd: str = Field("edgehub-lora-detector", alias="DETECTOR_CMD")

    # Extra args passed verbatim to the detector (advanced tuning)
    # Example:
    #   DETECTOR_ARGS="--fft-size 2048 --hop 256 --threshold-db 6"
    detector_args: str = Field("", alias="DETECTOR_ARGS")


class PlotterSettings(BaseSettings):
    nfft: int = 8192
    window: str = "hann"
    port: int = 8080


class AppSettings(BaseSettings):
    # Load from a .env file if it exists
    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__")

    kafka: KafkaSettings = KafkaSettings()
    plotter: PlotterSettings = PlotterSettings()


# Global instance to be used across the module
settings = AppSettings()

# Global shutdown event for graceful termination
shutdown_event = threading.Event()

# Optional Kafka producer for detections
_producer_ref = None

# Latest detections per base_path (for UI exploration)
_latest_det_by_base: dict[str, dict] = {}
_latest_det_lock = threading.Lock()

# Global consumer reference for cleanup
_consumer_ref = None
_consumer_thread_ref = None
_cleanup_done = threading.Lock()
_cleanup_started = False

def kafka_worker(plotter):
    global _consumer_ref, _producer_ref

    # Map Pydantic fields to the format confluent-kafka expects
    kafka_conf = {
        "bootstrap.servers": settings.kafka.bootstrap_servers,
        "group.id": settings.kafka.group_id,
        "auto.offset.reset": settings.kafka.auto_offset_reset,
        # Enable auto-commit for cleaner shutdown
        "enable.auto.commit": True,
        "auto.commit.interval.ms": 1000,
        # Reduce session timeout so Kafka detects failures faster
        "session.timeout.ms": 6000,
        "heartbeat.interval.ms": 2000,
        # Ensure we close cleanly on timeout
        "enable.auto.offset.store": True,
    }

    consumer = Consumer(kafka_conf)
    _consumer_ref = consumer  # Store global reference for cleanup
    consumer.subscribe([settings.kafka.topic])

    producer = None
    if settings.kafka.enable_detector:
        producer = Producer({"bootstrap.servers": settings.kafka.bootstrap_servers})
        _producer_ref = producer
        print(f"Detector enabled; publishing to {settings.kafka.detections_topic}")
        print(f"Detector command: {settings.kafka.detector_cmd}")

    print(f"Watching {settings.kafka.topic} on {settings.kafka.bootstrap_servers}...")
    print(f"Consumer group: {settings.kafka.group_id}")
    try:
        while not shutdown_event.is_set():
            try:
                msg = consumer.poll(1.0)
                if msg is None:
                    continue
                if msg.error():
                    print(f"Consumer error: {msg.error()}")
                    continue

                event_data = json.loads(msg.value().decode("utf-8"))
                meta_path = event_data.get("meta_file_path")
                data_path = event_data.get("data_file_path")

                # Process the IQ data and update the web UI
                if os.path.exists(meta_path) and os.path.exists(data_path):
                    base_path = meta_path[:-11]  # Remove ".sigmf-meta" suffix
                    try:
                        plotter.add_base(base_path)
                    except Exception as add_error:
                        print(f"Error processing {base_path}: {add_error}")
                        import traceback
                        traceback.print_exc()

                    # Optional: run detector and publish results back into Kafka
                    if producer is not None:
                        try:
                            det = run_detector(meta_path, data_path)
                            det_event = build_detection_event(event_data, meta_path, data_path, base_path, det)
                            # cache for UI
                            with _latest_det_lock:
                                _latest_det_by_base[base_path] = det_event
                            producer.produce(
                                settings.kafka.detections_topic,
                                json.dumps(det_event).encode("utf-8"),
                            )
                            producer.poll(0)

                            # Optional: forward to NGVA Data Bus adapter (thin bridge)
                            if os.getenv("ENABLE_NGVA_ADAPTER", "0") in ("1", "true", "TRUE", "yes", "YES"):
                                if ngva_adapter is None:
                                    print("NGVA adapter enabled but ngva_adapter.py not available")
                                else:
                                    try:
                                        ngva_adapter.send(det_event)
                                    except Exception as ngva_err:
                                        print(f"NGVA adapter error: {ngva_err}")
                        except Exception as det_err:
                            print(f"Detector error for {base_path}: {det_err}")
                            import traceback
                            traceback.print_exc()
                else:
                    print(f"Warning: Files not found - meta: {meta_path}, data: {data_path}")
            except Exception as loop_error:
                print(f"Error in message loop: {loop_error}")
                import traceback
                traceback.print_exc()
                # Continue the loop even if one message fails
                continue
    except Exception as e:
        print(f"Worker Error: {e}")
    finally:
        print("Kafka worker shutting down gracefully...")
        try:
            if producer is not None:
                try:
                    producer.flush(2.0)
                except Exception as flush_error:
                    print(f"Error flushing producer: {flush_error}")
            consumer.close()
            print("Kafka consumer closed.")
        except Exception as close_error:
            print(f"Error closing consumer: {close_error}")
        finally:
            _consumer_ref = None
            _producer_ref = None


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def detector_version() -> str:
    """Best-effort detector version string."""
    try:
        res = subprocess.run([settings.kafka.detector_cmd, "--version"], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def run_detector(meta_path: str, data_path: str) -> dict:
    """Run edgehub-lora-detector and return parsed JSON."""
    import shlex

    extra_args = []
    if settings.kafka.detector_args:
        # shlex to support quoted strings and avoid naive .split()
        extra_args = shlex.split(settings.kafka.detector_args)

    cmd = [
        settings.kafka.detector_cmd,
        "--meta",
        meta_path,
        "--data",
        data_path,
        *extra_args,
    ]
    # Keep stderr so we can debug failures
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"detector failed (code={res.returncode}) stdout={res.stdout[-4000:]} stderr={res.stderr[-4000:]} cmd={cmd}"
        )
    return json.loads(res.stdout)


def build_detection_event(source_event: dict, meta_path: str, data_path: str, base_path: str, det: dict) -> dict:
    # Envelope to keep provenance + allow schema evolution.
    return {
        "event_type": "detections_computed",
        "schema_version": 1,
        "computed_at": _utc_now_iso(),
        "source_event": source_event,
        "artifact": {
            "base_path": base_path,
            "meta_file_path": meta_path,
            "data_file_path": data_path,
        },
        "detector": {
            "name": "edgehub-lora-detector",
            "cmd": settings.kafka.detector_cmd,
            "version": detector_version(),
        },
        "detections": det,
    }


def cleanup_consumer():
    """Cleanup function to close Kafka consumer gracefully"""
    global _cleanup_started

    # Prevent multiple invocations
    with _cleanup_done:
        if _cleanup_started:
            print("Cleanup already in progress, skipping...")
            return
        _cleanup_started = True

    print("Cleanup handler triggered...")
    shutdown_event.set()

    # Don't wait for thread, just signal it to stop
    # The thread will close the consumer itself in the finally block
    print("Signaled Kafka worker to stop...")

    # Give a brief moment for the worker to exit naturally
    # but don't block indefinitely
    if _consumer_thread_ref and _consumer_thread_ref.is_alive():
        _consumer_thread_ref.join(timeout=2)
        if _consumer_thread_ref.is_alive():
            print("Worker still running after 2s, proceeding with shutdown...")


def run_local_consumer():
    global _consumer_thread_ref

    # Initialize using validated settings
    plotter = SigMFPanoramaPlotter(
        nfft=settings.plotter.nfft, window=settings.plotter.window
    )

    # Start Kafka in a background thread as daemon.
    # For local UI-only testing (no Kafka available), set DISABLE_KAFKA=true.
    if os.getenv("DISABLE_KAFKA", "0") in ("1", "true", "TRUE", "yes", "YES"):
        print("DISABLE_KAFKA=true -> Kafka worker not started (UI-only mode)")
    else:
        # Daemon threads don't block process exit
        thread = threading.Thread(target=kafka_worker, args=(plotter,), daemon=True)
        _consumer_thread_ref = thread
        thread.start()

    # Register cleanup handler - will run before daemon threads are killed
    atexit.register(cleanup_consumer)

    # Define the main page with @ui.page decorator
    @ui.page('/')
    async def index_page():
        import asyncio
        plotter._loop = asyncio.get_running_loop()

        ui.label('iq-stream-consumer').classes('text-2xl font-bold')

        with ui.tabs().classes('w-full') as tabs:
            tab_live = ui.tab('Live spectrum')
            tab_dechirp = ui.tab('Dechirp demo')

        with ui.tab_panels(tabs, value=tab_live).classes('w-full'):
            with ui.tab_panel(tab_live):
                plotter.build_ui()

            with ui.tab_panel(tab_dechirp):
                ui.markdown(
                    """This demo collapses LoRa chirps into a narrow tone by **dechirping** using the detector's
estimated chirp slope. It's a visualization tool (no payload decode).

Requirements:
- ENABLE_DETECTOR=true (so we have chirp metrics)
- detector must emit controller fingerprints (edgehub-lora-detector)
"""
                )

                base_select = ui.select(options=[], label='Capture base_path (from Kafka cache)').classes('w-full')

                with ui.row().classes('w-full items-end'):
                    meta_in = ui.input(label='Or meta_file_path (local)', placeholder='/path/to/foo.sigmf-meta').classes('w-full')
                    data_in = ui.input(label='data_file_path (local)', placeholder='/path/to/foo.sigmf-data').classes('w-full')
                    ui.button('Run detector on paths', on_click=lambda: run_detector_on_paths()).props('outline')

                ctrl_select = ui.select(options=[], label='Controller').classes('w-full')

                with ui.row().classes('w-full'):
                    fig_a = go.Figure(layout=go.Layout(template='plotly_dark'))
                    fig_b = go.Figure(layout=go.Layout(template='plotly_dark'))
                    plot_a = ui.plotly(fig_a).classes('w-1/2 h-[28rem]')
                    plot_b = ui.plotly(fig_b).classes('w-1/2 h-[28rem]')

                fig_peak = go.Figure(layout=go.Layout(template='plotly_dark'))
                peak_plot = ui.plotly(fig_peak).classes('w-full h-64')

                def refresh_bases():
                    with _latest_det_lock:
                        bases = sorted(_latest_det_by_base.keys())
                    base_select.options = bases
                    if bases and (base_select.value not in bases):
                        base_select.value = bases[-1]

                def _get_event_for_ui():
                    # Prefer explicit paths (local test mode). Otherwise use Kafka cache.
                    mp = (meta_in.value or '').strip()
                    dp = (data_in.value or '').strip()
                    if mp and dp:
                        base_path = mp[:-11] if mp.endswith('.sigmf-meta') else mp
                        return {
                            'artifact': {
                                'base_path': base_path,
                                'meta_file_path': mp,
                                'data_file_path': dp,
                            },
                            'detections': _local_det_cache.get('detections') if _local_det_cache else None,
                        }

                    b = base_select.value
                    if not b:
                        return None
                    with _latest_det_lock:
                        return _latest_det_by_base.get(b)

                def refresh_controllers():
                    ev = _get_event_for_ui()
                    det = (ev or {}).get('detections') or {}
                    ctrls = det.get('controllers') or []

                    opts = []
                    for c in ctrls:
                        cid = c.get('controller_id')
                        fp = c.get('fingerprint') or {}
                        bins = fp.get('bins') or {}
                        label = f"controller_id={cid}  fp={c.get('fingerprint_id','')}  center={bins.get('center_hz','?')}  bw={bins.get('bandwidth_hz','?')}"
                        opts.append({'label': label, 'value': cid})

                    ctrl_select.options = opts
                    if opts and (ctrl_select.value not in [o['value'] for o in opts]):
                        ctrl_select.value = opts[0]['value']

                def on_base_change(_):
                    # Clear local path mode when selecting from cache
                    refresh_controllers()

                base_select.on('update:model-value', on_base_change)

                _local_det_cache = {}

                def run_detector_on_paths():
                    mp = (meta_in.value or '').strip()
                    dp = (data_in.value or '').strip()
                    if not mp or not dp:
                        ui.notify('Provide meta_file_path and data_file_path', type='warning')
                        return
                    try:
                        d = run_detector(mp, dp)
                    except Exception as e:
                        ui.notify(f'Detector failed: {e}', type='negative')
                        return
                    _local_det_cache['detections'] = d
                    ui.notify('Detector OK; controllers loaded', type='positive')
                    refresh_controllers()

                def render():
                    if make_dechirp_waterfall is None or make_shifted_waterfall is None:
                        ui.notify('dechirp_tools not available in this drop-in', type='negative')
                        return

                    ev = _get_event_for_ui()
                    if not ev:
                        ui.notify('Select a base_path or provide local paths', type='warning')
                        return

                    cid = ctrl_select.value
                    if not cid:
                        ui.notify('Select a controller', type='warning')
                        return

                    det = ev.get('detections') or {}
                    ctrls = det.get('controllers') or []
                    c = next((x for x in ctrls if x.get('controller_id') == cid), None)
                    if not c:
                        ui.notify('Controller not found', type='warning')
                        return

                    fp = c.get('fingerprint') or {}
                    slope = fp.get('chirp_slope_hz_per_s')
                    if slope is None:
                        ui.notify('No chirp slope available (try another controller/capture)', type='warning')
                        return

                    tb = c.get('time_bounds_s') or {}
                    t_start = float(tb.get('start', 0.0))
                    t_end = float(tb.get('end', 0.0))

                    meta_path = (ev.get('artifact') or {}).get('meta_file_path')
                    data_path = (ev.get('artifact') or {}).get('data_file_path')
                    if not meta_path or not data_path:
                        ui.notify('Missing artifact paths', type='negative')
                        return

                    meta = load_sigmf_meta(meta_path)
                    fs_hz, fc_hz, *_ = parse_sigmf_params(meta)

                    fb = c.get('freq_bounds_hz') or {}
                    target_center_hz = float(fb.get('center', fc_hz))

                    try:
                        tt0, ff0, p0, pkf0, pkdb0 = make_shifted_waterfall(
                            meta_path=meta_path,
                            data_path=data_path,
                            t_start_s=t_start,
                            t_end_s=t_end,
                            capture_center_hz=float(fc_hz),
                            target_center_hz=float(target_center_hz),
                            pad_s=0.05,
                            nfft=2048,
                            hop=512,
                        )
                        tt1, ff1, p1, pkf1, pkdb1 = make_dechirp_waterfall(
                            meta_path=meta_path,
                            data_path=data_path,
                            t_start_s=t_start,
                            t_end_s=t_end,
                            capture_center_hz=float(fc_hz),
                            target_center_hz=float(target_center_hz),
                            slope_hz_per_s=float(slope),
                            pad_s=0.05,
                            nfft=2048,
                            hop=512,
                        )
                    except Exception as e:
                        ui.notify(f'render failed: {e}', type='negative')
                        return

                    # Left: shifted (raw). Right: shifted+dechirped.
                    fig0 = go.Figure(
                        data=go.Heatmap(
                            x=tt0,
                            y=ff0 / 1e3,
                            z=p0.T,
                            colorscale='Viridis',
                            zsmooth='best',
                            colorbar=dict(title='dB'),
                        ),
                        layout=go.Layout(
                            template='plotly_dark',
                            title='Shifted to controller center (raw chirps)',
                            xaxis_title='time (s)',
                            yaxis_title='freq (kHz, relative)',
                            margin=dict(l=40, r=10, t=40, b=40),
                        ),
                    )
                    fig1 = go.Figure(
                        data=go.Heatmap(
                            x=tt1,
                            y=ff1 / 1e3,
                            z=p1.T,
                            colorscale='Viridis',
                            zsmooth='best',
                            colorbar=dict(title='dB'),
                        ),
                        layout=go.Layout(
                            template='plotly_dark',
                            title='Shifted + dechirped (chirp collapses to tone)',
                            xaxis_title='time (s)',
                            yaxis_title='freq (kHz, relative)',
                            margin=dict(l=40, r=10, t=40, b=40),
                        ),
                    )
                    plot_a.update_figure(fig0)
                    plot_b.update_figure(fig1)

                    # Peak track comparison
                    f0_khz = pkf0 / 1e3
                    f1_khz = pkf1 / 1e3
                    figp = go.Figure(
                        data=[
                            go.Scatter(x=tt0, y=f0_khz, mode='lines', name='peak freq (raw shifted)'),
                            go.Scatter(x=tt1, y=f1_khz, mode='lines', name='peak freq (dechirped)'),
                        ],
                        layout=go.Layout(
                            template='plotly_dark',
                            title='Peak frequency track (should flatten after dechirp)',
                            xaxis_title='time (s)',
                            yaxis_title='peak freq (kHz, relative)',
                            margin=dict(l=40, r=10, t=40, b=40),
                        ),
                    )
                    peak_plot.update_figure(figp)

                with ui.row().classes('w-full'):
                    ui.button('Refresh list', on_click=lambda: (refresh_bases(), refresh_controllers()))
                    ui.button('Render side-by-side + peak track', on_click=render)

                # Initial population
                refresh_bases()
                refresh_controllers()
    
    # Start the UI (The "Blocking" call)
    # Use reload=False for stability in containerized/threaded setups
    # show=False prevents browser auto-open in container
    ui.run(
        port=settings.plotter.port,
        reload=False,
        show=False,
        # This tells uvicorn to handle signals properly
        uvicorn_logging_level='info'
    )


if __name__ in {"__main__", "__mp_main__"}:
    run_local_consumer()
