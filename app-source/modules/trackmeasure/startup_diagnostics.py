"""Opt-in build validation; inactive during ordinary user launches."""
import json
import os
from pathlib import Path
import platform
import sys
import time
import traceback
from PySide6.QtCore import QTimer
from app_runtime import APP_VERSION, settings_path


def record_ready(window, started):
    target = Path(os.environ['PCA_STARTUP_TEST_REPORT'])
    bos_deadline = time.monotonic() + 25
    def inspect():
        import numpy
        import cv2
        import PySide6
        import imageio_ffmpeg
        result = dict(version=APP_VERSION, machine=platform.machine(), python=sys.version.split()[0],
                      numpy=numpy.__version__, opencv=cv2.__version__, qt=PySide6.__version__,
                      ready_seconds=round(time.perf_counter()-started, 3),
                      settings=settings_path(), cine=window.cine_path,
                      ffmpeg=imageio_ffmpeg.get_ffmpeg_exe(), frozen=bool(getattr(sys, 'frozen', False)))
        if window.cine_path:
            frame = window.vm.cine_handler.get_img(0)
            result.update(frame_shape=list(frame.shape), frame_dtype=str(frame.dtype),
                          frame_rate=float(window.vm.cine_handler.metadata.FrameRate))
            if os.environ.get('PCA_STARTUP_TEST_EXPORT'):
                from video_export import export_clip
                from viewer_processing import default_grade
                output = target.with_suffix('.mp4')
                settings = dict(image_brightness=0, image_contrast=1, image_gamma=1,
                                toggle_ftone=False, raw_enabled=False)
                export_clip(window.cine_path, output, 0, 5, 30, settings, default_grade())
                video = cv2.VideoCapture(str(output))
                try:
                    decoded, frame = video.read()
                    result['export'] = dict(path=str(output), decoded=bool(decoded),
                                          frames=int(video.get(cv2.CAP_PROP_FRAME_COUNT)),
                                          fps=video.get(cv2.CAP_PROP_FPS))
                    if not decoded or result['export']['frames'] != 6:
                        raise RuntimeError('Export could not be decoded as six frames')
                finally:
                    video.release()
        if os.environ.get('PCA_STARTUP_TEST_BOS') == '1':
            panel = window.bos_panel
            if window.workflow_stack.currentWidget() is not panel or not panel.last_result:
                raise RuntimeError('BOS job did not restore an image: ' + panel.status.text())
            field = panel.last_result['field']
            result['bos'] = dict(frame=panel.last_result['index'], settings=panel.settings(),
                                 measured_patches=int(numpy.sum(field.valid)), total_patches=int(field.valid.size),
                                 status=panel.status.text())
            if os.environ.get('PCA_STARTUP_TEST_BOS_VECTORS') == '1':
                from bos_vector_data import save_vector_csv
                sample = panel.last_result.get('vector_samples')
                settings = panel.settings()
                if sample is None or not len(sample.rows) or not settings['show_vectors']:
                    raise RuntimeError('The vector job did not display any measured arrows.')
                csv_path = target.with_suffix('.vectors.csv')
                result['bos']['arrows'] = len(sample.rows)
                result['bos']['csv_rows'] = save_vector_csv(csv_path, panel.last_result, settings)
        target.parent.mkdir(parents=True, exist_ok=True)
        window.grab().save(str(target.with_suffix('.png')))
        target.write_text(json.dumps(result, indent=2), encoding='utf-8')
    def finish():
        if os.environ.get('PCA_STARTUP_TEST_QUIT') == '1':
            QTimer.singleShot(150, window.close)

    def capture():
        if (os.environ.get('PCA_STARTUP_TEST_BOS') == '1' and window.bos_panel.last_result is None
                and time.monotonic() < bos_deadline):
            QTimer.singleShot(100, window, capture)
            return
        try:
            inspect()
            if os.environ.get('PCA_STARTUP_TEST_BOS_PREVIEW') == '1':
                probe_bos_preview(window, target, finish)
                return
            if os.environ.get('PCA_STARTUP_TEST_TRANSPORT') == '1':
                probe_transport(window, target, finish)
                return
        except Exception:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({'error': traceback.format_exc()}), encoding='utf-8')
        finish()
    QTimer.singleShot(500, capture)


def probe_bos_preview(window, report, finished):
    """Opt-in installed-app check using the real range buttons and result window."""
    import cv2
    from bos_preview import export_cached_bos
    panel = window.bos_panel
    panel.pause()
    first = panel.last_result['index']
    last = min(panel.last_result['count']-1, first+119)
    panel.analyze_range.setChecked(True)
    panel.range_bar.first.setValue(panel.last_result['first']+first)
    panel.range_bar.last.setValue(panel.last_result['first']+last)
    check_sampling = os.environ.get('PCA_STARTUP_TEST_BOS_SAMPLING') == '1'
    if check_sampling:
        bar = panel.range_bar
        bar.sampling.setCurrentIndex(bar.sampling.findData('match'))
        bar.minimum_fps.setValue(200)
        bar.target_fps.setCurrentIndex(bar.target_fps.findData(60))
        if bar.sample_step() != 3:
            raise RuntimeError('200 source fps at 60 fps delivery should measure every third frame.')
        window.grab().save(str(report.with_suffix('.range.png')))
    if not panel.range_bar.analyze.isEnabled():
        raise RuntimeError('BOS range Analyze button did not become ready.')
    panel.range_bar.analyze.click()
    players = getattr(panel, '_bos_preview_windows', [])
    if not players:
        raise RuntimeError('BOS range analysis did not open its player.')
    player = players[-1]
    samples = []
    player.view.framePainted.connect(lambda index: samples.append((time.perf_counter(),index)))
    deadline = time.monotonic()+25
    data = dict(frames=player.clip.count, source_first=player.clip.source_frame(0),
                source_last=player.clip.source_frame(player.clip.count-1),
                settings=player.clip.manifest['settings'], anchor_failures=player.clip.manifest['anchor_failures'])

    def fail():
        result=json.loads(report.read_text());result['error']=traceback.format_exc()
        report.write_text(json.dumps(result,indent=2),encoding='utf-8')
        player.close();finished()

    def poll(stage=0):
        try:
            if time.monotonic()>deadline:
                raise RuntimeError('Installed BOS preview validation timed out.')
            if stage==0:
                if player.pending_seek is not None:
                    QTimer.singleShot(20,window,poll);return
                if os.environ.get('PCA_STARTUP_TEST_BOS_COMPASS') == '1':
                    if not player.compass_button.isVisible() or not player.compass_button.isChecked() or player.view.compass is None:
                        raise RuntimeError('The direction compass should initially be visible on the footage.')
                if player.studio.isVisible():
                    raise RuntimeError('Record Studio must start collapsed.')
                player.studio_button.click()
                if not player.studio.isVisible() or player.studio.output_fps() != 60:
                    raise RuntimeError('Record Studio failed to open with the sharing preset.')
                if check_sampling:
                    offsets = list(range(first, last+1, 3))
                    if offsets[-1] != last:
                        offsets.append(last)
                    expected = [panel.last_result['first']+n for n in offsets]
                    actual = [player.clip.source_frame(i) for i in range(player.clip.count)]
                    if actual != expected or player.fps.value() != 200:
                        raise RuntimeError('Sampled preview did not retain the planned speed and source frames.')
                    data['sample_step'] = 3
                    if abs(player.edit_model.duration-(last-first+1)/200.) > 1e-8:
                        raise RuntimeError('Sampled preview changed the duration of the marked range.')
                else:
                    player.fps.setValue(240)
                if os.environ.get('PCA_STARTUP_TEST_BOS_STUDIO') == '1':
                    from bos_camera_labels import camera_lines
                    data['studio_rows'] = [player.studio.tabs[i].text() for i in player.studio.track.rows]
                    data['camera_caption'] = camera_lines(player.clip, {'camera_model','resolution','exposure'}, 0)
                    data['vector_key_visible'] = player.view.vector_key_card is not None
                player.play.click()
                QTimer.singleShot(20,window,lambda:poll(1));return
            if stage==1:
                if player.playing:
                    QTimer.singleShot(20,window,lambda:poll(1));return
                if player.position!=player.clip.count-1:
                    raise RuntimeError('Cached BOS playback did not reach its final frame.')
                data['painted_frames']=len(samples)
                data['last_index']=player.position
                data['skipped']=player.skipped
                if len(samples)>1:
                    data['painted_fps']=(len(samples)-1)/(samples[-1][0]-samples[0][0])
                player.grab().save(str(report.with_suffix('.bos-preview.png')))
                output=report.with_suffix('.bos-preview.mp4')
                placement = player.view.compass
                fps = player.studio.output_fps()
                export_cached_bos(player.clip, output, fps, studio=player.edit_model, compass=placement,
                                  vector_key=player.vector_key_placement)
                data['edited_duration_seconds'] = player.edit_model.duration
                if placement is not None:
                    data['compass'] = dict(x=placement.x, y=placement.y, size=placement.size)
                cap=cv2.VideoCapture(str(output))
                try:
                    decoded=0
                    while cap.read()[0]:decoded+=1
                    data['export']=dict(path=str(output),decoded_frames=decoded,fps=cap.get(cv2.CAP_PROP_FPS))
                    if decoded != player.edit_model.output_count(fps) or abs(data['export']['fps']-fps) > .001:
                        raise RuntimeError('Record Studio export frame count or rate differs.')
                finally:cap.release()
                player.shutdown()
                QTimer.singleShot(20,window,lambda:poll(2));return
            if not player.cleaned:
                QTimer.singleShot(20,window,lambda:poll(2));return
            data['cache_removed']=not player.clip.directory.exists()
            result=json.loads(report.read_text());result['bos_preview']=data
            report.write_text(json.dumps(result,indent=2),encoding='utf-8')
            finished()
        except Exception:fail()
    QTimer.singleShot(20,window,poll)

def probe_transport(window, report, finished):
    """Opt-in release check: drive the shipped buttons and observe the real playhead."""
    from PySide6.QtCore import Qt
    from statistics import median

    scenarios = [('play', window.forward_play_button), ('fast_forward', window.forward_faster_button),
                 ('reverse', window.reverse_play_button)]
    results = {}
    def run(index):
        name, button = scenarios[index]
        values = []
        def moved(frame):
            values.append((time.perf_counter(), int(frame)))
        window.frame_slider.valueChanged.connect(moved)
        start = time.perf_counter()
        button.click()
        def pause():
            entered = time.perf_counter()
            window.pause_button.click()
            stopped = time.perf_counter()
            at_pause = window.frame_slider.value()
            def verify():
                window.frame_slider.valueChanged.disconnect(moved)
                actual = window.frame_slider.value()
                samples = [(t, f) for t, f in values if t <= stopped]
                intervals = [1000*(b[0]-a[0]) for a,b in zip(samples,samples[1:])]
                results[name] = dict(updates=len(samples), first_ms=round(1000*(samples[0][0]-start),1) if samples else None,
                    median_interval_ms=round(median(intervals),1) if intervals else None,
                    pause_dispatch_late_ms=round(max(0,1000*(entered-start)-2000),1),
                    pause_action_ms=round(1000*(stopped-entered),1), still_paused=actual==at_pause and not window._playback_active,
                    first_frame=samples[0][1] if samples else None, last_frame=at_pause)
                if index+1 < len(scenarios):
                    run(index+1)
                else:
                    result=json.loads(report.read_text())
                    result['transport']=results
                    report.write_text(json.dumps(result,indent=2),encoding='utf-8')
                    finished()
            QTimer.singleShot(200,window,verify)
        deadline = QTimer(window)
        deadline.setTimerType(Qt.TimerType.PreciseTimer)
        deadline.setSingleShot(True)
        deadline.timeout.connect(pause)
        deadline.timeout.connect(deadline.deleteLater)
        deadline.start(max(1,round(2000-1000*(time.perf_counter()-start))))
    run(0)
