/* =========================================================
   Supercut · 前端逻辑（原生 JS，无框架、无构建）
   两页共用：主题切换 + 登录页交互 + 主页面剪辑逻辑
   ========================================================= */
(function () {
  'use strict';

  var THEME_KEY = 'supercut.theme';
  var THEMES = ['light', 'gray', 'dark'];
  var ALLOWED_EXT = ['mp4', 'm4v', 'mov', 'mkv', 'webm', 'avi', 'flv', 'wmv', 'ts', 'mts', '3gp'];
  var MIN_CLIP = 0.1;
  var MIN_CROP = 16;

  /* ---------- 主题 ---------- */

  function readStoredTheme() {
    try {
      var value = window.localStorage.getItem(THEME_KEY);
      if (THEMES.indexOf(value) !== -1) return value;
    } catch (err) {
      /* localStorage 不可用时回退默认 gray */
    }
    return 'gray';
  }

  function themeColor(theme) {
    if (theme === 'light') return '#fbfbf9';
    if (theme === 'dark') return '#141617';
    return '#e9e9e5';
  }

  function applyTheme(theme) {
    if (THEMES.indexOf(theme) === -1) theme = 'gray';
    document.documentElement.setAttribute('data-theme', theme);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', themeColor(theme));
    var inputs = document.querySelectorAll('input[name="theme"]');
    for (var i = 0; i < inputs.length; i++) {
      inputs[i].checked = inputs[i].value === theme;
    }
  }

  function initThemeSwitch() {
    applyTheme(readStoredTheme());
    var inputs = document.querySelectorAll('input[name="theme"]');
    for (var i = 0; i < inputs.length; i++) {
      inputs[i].addEventListener('change', function (ev) {
        var theme = ev.target && ev.target.value;
        if (THEMES.indexOf(theme) === -1) return;
        applyTheme(theme);
        try {
          window.localStorage.setItem(THEME_KEY, theme);
        } catch (err) {
          /* 隐私模式下不持久化，但不影响本次切换 */
        }
      });
    }
  }

  // 脚本在 <head> 同步加载，尽早应用已保存主题，避免闪烁
  applyTheme(readStoredTheme());

  /* ---------- 通用工具 ---------- */

  function $(id) {
    return document.getElementById(id);
  }

  function on(el, type, handler, options) {
    if (el) el.addEventListener(type, handler, options);
  }

  function clamp(value, min, max) {
    if (max < min) max = min;
    return Math.min(max, Math.max(min, value));
  }

  function round2(value) {
    return Math.round(value * 100) / 100;
  }

  function fmtSeconds2(value) {
    var n = Number(value);
    if (!isFinite(n) || n < 0) n = 0;
    return (Math.round(n * 100) / 100).toFixed(2);
  }

  function fmtClock(seconds) {
    var total = Number(seconds);
    if (!isFinite(total) || total < 0) total = 0;
    total = Math.round(total * 100) / 100;
    var hours = Math.floor(total / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    var secs = total % 60;
    var secsText = (secs < 10 ? '0' : '') + secs.toFixed(2);
    if (hours > 0) {
      return hours + ':' + (minutes < 10 ? '0' : '') + minutes + ':' + secsText;
    }
    return minutes + ':' + secsText;
  }

  function fmtDurationText(seconds) {
    var n = Number(seconds);
    if (!isFinite(n) || n <= 0) return '—';
    if (n < 60) return fmtSeconds2(n) + ' 秒';
    return fmtClock(n);
  }

  function fmtSize(bytes) {
    var n = Number(bytes);
    if (!isFinite(n) || n < 0) return '—';
    var units = ['B', 'KB', 'MB', 'GB', 'TB'];
    var index = 0;
    while (n >= 1024 && index < units.length - 1) {
      n /= 1024;
      index += 1;
    }
    var digits = index === 0 ? 0 : n >= 100 ? 0 : n >= 10 ? 1 : 2;
    return n.toFixed(digits) + ' ' + units[index];
  }

  // 相对路径解析：兼容子路径部署（/supercut/）与本地根路径（/）
  function basePath() {
    try {
      return new URL(document.baseURI).pathname.replace(/\/+$/, '');
    } catch (err) {
      return '';
    }
  }

  function resolveUrl(path) {
    var raw = String(path == null ? '' : path);
    if (/^(?:[a-z][a-z0-9+.-]*:)?\/\//i.test(raw)) return raw;
    var root = basePath();
    var clean = raw.charAt(0) === '/' ? raw : '/' + raw;
    if (root && (clean === root || clean.indexOf(root + '/') === 0)) return clean;
    return root + clean;
  }

  function apiUrl(path) {
    return resolveUrl(path);
  }

  function loginUrl() {
    return resolveUrl('login');
  }

  function fileExt(name) {
    var text = String(name || '');
    var dot = text.lastIndexOf('.');
    return dot === -1 ? '' : text.slice(dot + 1).toLowerCase();
  }

  /* ---------- Toast / 登录失效 ---------- */

  function showToast(message, type) {
    var stack = $('toast-stack');
    if (!stack) return;
    var kind = type || 'error';
    var el = document.createElement('div');
    el.className = 'toast toast--' + kind;
    el.setAttribute('role', kind === 'error' ? 'alert' : 'status');

    var icon = document.createElement('span');
    icon.className = 'toast__icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = kind === 'error' || kind === 'warn' ? '!' : kind === 'success' ? '✓' : 'i';

    var text = document.createElement('span');
    text.className = 'toast__text';
    text.textContent = String(message == null ? '' : message);

    el.appendChild(icon);
    el.appendChild(text);
    stack.appendChild(el);

    var dismissed = false;
    function dismiss() {
      if (dismissed) return;
      dismissed = true;
      window.clearTimeout(timer);
      el.classList.remove('is-visible');
      window.setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 240);
    }
    var timer = window.setTimeout(dismiss, kind === 'error' ? 5200 : 3800);
    el.addEventListener('click', dismiss);

    window.requestAnimationFrame(function () {
      el.classList.add('is-visible');
    });
  }

  function redirectToLogin(message) {
    showToast(message || '登录已过期，正在跳转登录页…', 'error');
    window.setTimeout(function () {
      window.location.href = loginUrl();
    }, 700);
  }

  /* ---------- HTTP ---------- */

  function makeHttpError(message, status) {
    var err = new Error(message);
    err.status = status || 0;
    return err;
  }

  function httpErrorMessage(status) {
    if (status === 400) return '请求参数有误';
    if (status === 401) return '登录已过期，请重新登录';
    if (status === 404) return '资源不存在或已过期';
    if (status === 413) return '文件超过上传上限';
    if (status >= 500) return '服务器处理失败，请稍后重试';
    return '请求失败（HTTP ' + status + '）';
  }

  async function requestJSON(path, options) {
    var opts = options || {};
    var headers = Object.assign({ Accept: 'application/json' }, opts.headers || {});
    var response;
    try {
      response = await fetch(apiUrl(path), Object.assign({}, opts, { headers: headers }));
    } catch (err) {
      throw makeHttpError('网络连接失败，请检查网络后重试', 0);
    }

    if (response.status === 401) {
      var authError = makeHttpError('登录已过期，请重新登录', 401);
      authError.isRedirect = true;
      redirectToLogin();
      throw authError;
    }

    var text = '';
    try {
      text = await response.text();
    } catch (err) {
      text = '';
    }
    var data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (err) {
        data = null;
      }
    }

    if (!response.ok) {
      var message = data && typeof data.error === 'string' && data.error
        ? data.error
        : httpErrorMessage(response.status);
      throw makeHttpError(message, response.status);
    }
    return data;
  }

  /* ---------- 状态 ---------- */

  var els = null;

  var state = {
    health: null,
    upload: null,
    objectUrl: null,
    duration: 0,
    videoWidth: 0,
    videoHeight: 0,
    mode: 'trim',
    trim: { start: 0, end: 0 },
    crop: { has: false, x: 0, y: 0, w: 0, h: 0 },
    previewing: false,
    exporting: false,
    uploading: false
  };

  /* ---------- 初始化 ---------- */

  function initLogin() {
    var form = $('login-form');
    if (!form) return;
    var username = form.querySelector('input[name="username"]');
    var password = form.querySelector('input[name="password"]');
    if (username && password && String(username.value || '').length > 0) {
      password.focus();
    }
    on(form, 'submit', function () {
      var button = form.querySelector('button[type="submit"]');
      if (!button || button.disabled) return;
      window.setTimeout(function () {
        button.disabled = true;
        button.textContent = '登录中…';
      }, 0);
    });
  }

  function initApp() {
    if (!$('editor')) return;

    els = {
      editor: $('editor'),
      dropzone: $('dropzone'),
      fileInput: $('file-input'),
      uploadProgress: $('upload-progress'),
      uploadFilename: $('upload-filename'),
      uploadPercent: $('upload-percent'),
      uploadBar: $('upload-bar'),
      uploadProgressbar: $('upload-progressbar'),
      uploadLimit: $('upload-limit'),
      fileCard: $('file-card'),
      changeVideo: $('btn-change-video'),
      metaName: $('meta-name'),
      metaDuration: $('meta-duration'),
      metaResolution: $('meta-resolution'),
      metaSize: $('meta-size'),
      video: $('video'),
      videoFrame: $('video-frame'),
      cropLayer: $('crop-layer'),
      cropSelection: $('crop-selection'),
      cropHint: $('crop-hint'),
      transport: $('transport'),
      transportTime: $('transport-time'),
      seek: $('seek'),
      playBtn: $('btn-play'),
      modeTrim: $('mode-trim'),
      modeCrop: $('mode-crop'),
      trimPanel: $('trim-panel'),
      cropPanel: $('crop-panel'),
      trimDuration: $('trim-duration'),
      trimFill: $('trim-fill'),
      trimStartRange: $('trim-start-range'),
      trimEndRange: $('trim-end-range'),
      trimStartInput: $('trim-start-input'),
      trimEndInput: $('trim-end-input'),
      setStart: $('btn-set-start'),
      setEnd: $('btn-set-end'),
      previewBtn: $('btn-preview'),
      exportTrim: $('btn-export-trim'),
      exportCrop: $('btn-export-crop'),
      cropX: $('crop-x'),
      cropY: $('crop-y'),
      cropW: $('crop-w'),
      cropH: $('crop-h'),
      cropSize: $('crop-size'),
      cropEmptyHint: $('crop-empty-hint'),
      selectAll: $('btn-select-all'),
      resetCrop: $('btn-reset-crop'),
      exportStatus: $('export-status'),
      exportStatusText: $('export-status-text'),
      result: $('result'),
      resultTitle: $('result-title'),
      resultMeta: $('result-meta'),
      resultDownload: $('result-download')
    };

    bindUpload();
    bindMode();
    bindTrim();
    bindCropInputs();
    bindCropDrag();
    bindCropButtons();
    bindTransport();
    bindExport();
    bindVideoEvents();

    loadHealth();
  }

  /* ---------- 健康检查 ---------- */

  async function loadHealth() {
    try {
      var data = await requestJSON('api/health');
      state.health = data && typeof data === 'object' ? data : {};
      var maxMb = Number(state.health.max_upload_mb);
      if (isFinite(maxMb) && maxMb > 0 && els.uploadLimit) {
        els.uploadLimit.textContent = '支持 mp4 / mov / mkv / webm 等格式，单个文件最大 ' + maxMb + ' MB';
      }
      if (state.health.ffmpeg === false) {
        showToast('服务端未检测到 FFmpeg，暂时无法导出视频。', 'warn');
      }
    } catch (err) {
      if (err && err.isRedirect) return;
      // 拿不到上限信息时不阻断使用，保留默认提示
      if (els.uploadLimit) els.uploadLimit.textContent = '支持 mp4 / mov / mkv / webm 等格式';
    }
  }

  /* ---------- 上传 ---------- */

  function bindUpload() {
    var dropzone = els.dropzone;
    var fileInput = els.fileInput;

    on(dropzone, 'click', function () {
      if (!state.uploading) fileInput.click();
    });

    on(dropzone, 'keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ' || ev.key === 'Spacebar') {
        ev.preventDefault();
        if (!state.uploading) fileInput.click();
      }
    });

    on(fileInput, 'change', function () {
      var file = fileInput.files && fileInput.files[0];
      fileInput.value = '';
      if (file) handleSelectedFile(file);
    });

    function hasFiles(ev) {
      var dt = ev.dataTransfer;
      if (!dt) return false;
      if (dt.types && typeof dt.types.indexOf === 'function') {
        return dt.types.indexOf('Files') !== -1;
      }
      return !!(dt.files && dt.files.length);
    }

    ['dragenter', 'dragover'].forEach(function (type) {
      on(dropzone, type, function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        if (!state.uploading) dropzone.classList.add('is-dragover');
      });
    });

    on(dropzone, 'dragleave', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      dropzone.classList.remove('is-dragover');
    });

    on(dropzone, 'drop', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      dropzone.classList.remove('is-dragover');
      if (state.uploading) return;
      var files = ev.dataTransfer && ev.dataTransfer.files;
      if (!files || !files.length) return;
      if (files.length > 1) showToast('一次只能上传一个视频，已选择第一个文件。', 'warn');
      handleSelectedFile(files[0]);
    });

    // 防止把文件拖到页面其他位置时浏览器直接打开文件
    on(document, 'dragover', function (ev) {
      if (hasFiles(ev)) ev.preventDefault();
    });
    on(document, 'drop', function (ev) {
      if (hasFiles(ev)) ev.preventDefault();
    });

    on(els.changeVideo, 'click', resetUpload);
  }

  function handleSelectedFile(file) {
    var ext = fileExt(file.name);
    if (ALLOWED_EXT.indexOf(ext) === -1) {
      showToast('不支持的视频格式：' + (ext ? '.' + ext : '未知格式'), 'error');
      return;
    }
    var maxMb = state.health ? Number(state.health.max_upload_mb) : NaN;
    if (isFinite(maxMb) && maxMb > 0 && file.size > maxMb * 1024 * 1024) {
      showToast('文件过大：' + fmtSize(file.size) + '，上限 ' + maxMb + ' MB', 'error');
      return;
    }
    startUpload(file);
  }

  function setUploadProgress(percent) {
    var value = clamp(Number(percent) || 0, 0, 100);
    if (els.uploadPercent) els.uploadPercent.textContent = Math.round(value) + '%';
    if (els.uploadBar) els.uploadBar.style.width = value.toFixed(1) + '%';
    if (els.uploadProgressbar) els.uploadProgressbar.setAttribute('aria-valuenow', String(Math.round(value)));
  }

  function startUpload(file) {
    state.uploading = true;
    els.dropzone.classList.add('is-busy');
    els.dropzone.setAttribute('aria-disabled', 'true');
    if (els.uploadFilename) els.uploadFilename.textContent = file.name;
    els.uploadProgress.hidden = false;
    setUploadProgress(0);

    var form = new FormData();
    form.append('file', file, file.name);

    var xhr = new XMLHttpRequest();
    xhr.open('POST', apiUrl('api/upload'), true);
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.upload.onprogress = function (ev) {
      if (ev.lengthComputable && ev.total > 0) setUploadProgress((ev.loaded / ev.total) * 100);
    };
    xhr.onload = function () {
      finishUpload();
      var data = null;
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch (err) {
        data = null;
      }
      if (xhr.status === 401) {
        redirectToLogin();
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300 && data && !data.error) {
        onUploaded(file, data);
        return;
      }
      var message = data && data.error ? data.error : httpErrorMessage(xhr.status);
      showToast('上传失败：' + message, 'error');
    };
    xhr.onerror = function () {
      finishUpload();
      showToast('网络错误，上传失败，请重试。', 'error');
    };
    xhr.onabort = function () {
      finishUpload();
    };
    xhr.send(form);

    function finishUpload() {
      state.uploading = false;
      els.dropzone.classList.remove('is-busy');
      els.dropzone.removeAttribute('aria-disabled');
      els.uploadProgress.hidden = true;
      setUploadProgress(0);
    }
  }

  function onUploaded(file, data) {
    var duration = Number(data.duration);
    var width = Math.round(Number(data.width));
    var height = Math.round(Number(data.height));

    state.upload = data;
    state.duration = isFinite(duration) && duration > 0 ? round2(duration) : 0;
    state.videoWidth = isFinite(width) && width > 0 ? width : 0;
    state.videoHeight = isFinite(height) && height > 0 ? height : 0;

    var displayName = data.name || file.name;
    els.metaName.textContent = displayName;
    els.metaName.title = displayName;
    els.metaDuration.textContent = fmtDurationText(state.duration);
    els.metaResolution.textContent = state.videoWidth && state.videoHeight
      ? state.videoWidth + ' × ' + state.videoHeight
      : '—';
    els.metaSize.textContent = fmtSize(Number(data.size) || file.size);

    els.dropzone.hidden = true;
    els.fileCard.hidden = false;
    els.editor.hidden = false;

    if (state.objectUrl) {
      URL.revokeObjectURL(state.objectUrl);
      state.objectUrl = null;
    }
    state.objectUrl = URL.createObjectURL(file);
    els.video.src = state.objectUrl;

    if (state.videoWidth && state.videoHeight) {
      setFrameAspect(state.videoWidth, state.videoHeight);
    }

    state.previewing = false;
    updatePreviewButton();
    clearCrop();
    resetTrim(state.duration);
    setMode('trim');
    maybeWarnResolution();
  }

  function maybeWarnResolution() {
    if (!state.videoWidth || !state.videoHeight) return;
    if (state.videoWidth < MIN_CROP || state.videoHeight < MIN_CROP) {
      showToast('视频分辨率过小，无法框选裁剪。', 'warn');
    }
  }

  function resetUpload() {
    if (state.uploading) return;
    state.upload = null;
    state.duration = 0;
    state.videoWidth = 0;
    state.videoHeight = 0;
    state.previewing = false;
    state.trim.start = 0;
    state.trim.end = 0;
    clearCrop();
    updatePreviewButton();

    if (state.objectUrl) {
      URL.revokeObjectURL(state.objectUrl);
      state.objectUrl = null;
    }
    els.video.pause();
    els.video.removeAttribute('src');
    try {
      els.video.load();
    } catch (err) {
      /* ignore */
    }
    els.videoFrame.style.aspectRatio = '';
    els.fileCard.hidden = true;
    els.dropzone.hidden = false;
    els.editor.hidden = true;
    els.exportStatus.hidden = true;
    els.result.hidden = true;
    setMode('trim');
  }

  function setFrameAspect(width, height) {
    if (width > 0 && height > 0) {
      els.videoFrame.style.aspectRatio = width + ' / ' + height;
    }
  }

  /* ---------- 视频事件 ---------- */

  function bindVideoEvents() {
    on(els.video, 'loadedmetadata', function () {
      var vw = Math.round(Number(els.video.videoWidth) || 0);
      var vh = Math.round(Number(els.video.videoHeight) || 0);
      if (vw > 0 && vh > 0) {
        state.videoWidth = vw;
        state.videoHeight = vh;
        setFrameAspect(vw, vh);
        if (state.crop.has) finalizeCrop();
      }

      var videoDuration = Number(els.video.duration);
      if (isFinite(videoDuration) && videoDuration > 0 && Math.abs(videoDuration - state.duration) > 0.05) {
        state.duration = round2(videoDuration);
        els.metaDuration.textContent = fmtDurationText(state.duration);
        resetTrim(state.duration);
      }
      updateTransportTime();
    });

    on(els.video, 'timeupdate', function () {
      updateTransportTime();
      if (state.mode === 'crop' && els.seek) {
        els.seek.value = String(els.video.currentTime || 0);
      }
      if (state.previewing && state.duration > 0 && els.video.currentTime >= state.trim.end) {
        stopPreview();
      }
    });

    on(els.video, 'play', function () {
      els.playBtn.classList.add('is-playing');
      els.playBtn.setAttribute('aria-label', '暂停');
    });

    on(els.video, 'pause', function () {
      els.playBtn.classList.remove('is-playing');
      els.playBtn.setAttribute('aria-label', '播放');
    });

    on(els.video, 'ended', function () {
      if (state.previewing) stopPreview();
    });

    on(els.video, 'error', function () {
      if (state.upload) {
        showToast('视频预览加载失败：浏览器可能不支持该格式，或页面安全策略限制了本地预览。', 'error');
      }
    });
  }

  /* ---------- 模式切换 ---------- */

  function bindMode() {
    on(els.modeTrim, 'click', function () {
      setMode('trim');
    });
    on(els.modeCrop, 'click', function () {
      setMode('crop');
    });
  }

  function setMode(mode) {
    state.mode = mode === 'crop' ? 'crop' : 'trim';
    var isCrop = state.mode === 'crop';

    els.modeTrim.classList.toggle('is-active', !isCrop);
    els.modeCrop.classList.toggle('is-active', isCrop);
    els.modeTrim.setAttribute('aria-pressed', isCrop ? 'false' : 'true');
    els.modeCrop.setAttribute('aria-pressed', isCrop ? 'true' : 'false');

    els.trimPanel.hidden = isCrop;
    els.cropPanel.hidden = !isCrop;
    els.cropLayer.hidden = !isCrop;
    els.cropHint.hidden = !isCrop;
    els.transport.hidden = !isCrop;
    // 框选模式下覆盖层会盖住原生控件，改用下方自定义播放条
    if (els.video) els.video.controls = !isCrop;

    if (isCrop) {
      renderCrop();
      updateTransportTime();
    } else {
      if (state.previewing) stopPreview();
    }
  }

  /* ---------- 剪片段 ---------- */

  function resetTrim(duration) {
    var total = Number(duration) > 0 ? Number(duration) : 0;
    state.trim.start = 0;
    state.trim.end = total;

    var sliderMax = String(Math.max(total, 0.01));
    els.trimStartRange.min = '0';
    els.trimStartRange.max = sliderMax;
    els.trimEndRange.min = '0';
    els.trimEndRange.max = sliderMax;
    els.trimStartInput.max = String(total);
    els.trimEndInput.max = String(total);
    syncTrimUI();
  }

  function setTrimStart(value) {
    if (!(state.duration > 0)) return;
    var maxStart = Math.max(0, state.trim.end - MIN_CLIP);
    state.trim.start = round2(clamp(Number(value) || 0, 0, maxStart));
    syncTrimUI();
  }

  function setTrimEnd(value) {
    if (!(state.duration > 0)) return;
    var minEnd = Math.min(state.duration, state.trim.start + MIN_CLIP);
    state.trim.end = round2(clamp(Number(value) || 0, minEnd, state.duration));
    syncTrimUI();
  }

  function syncTrimUI() {
    var total = state.duration;
    var max = total > 0 ? total : 1;
    var start = state.trim.start;
    var end = state.trim.end;

    els.trimStartRange.value = String(start);
    els.trimEndRange.value = String(end);
    els.trimFill.style.left = (clamp(start / max, 0, 1) * 100).toFixed(3) + '%';
    els.trimFill.style.width = (clamp((end - start) / max, 0, 1) * 100).toFixed(3) + '%';

    if (document.activeElement !== els.trimStartInput) {
      els.trimStartInput.value = fmtSeconds2(start);
    }
    if (document.activeElement !== els.trimEndInput) {
      els.trimEndInput.value = fmtSeconds2(end);
    }

    els.trimDuration.textContent = fmtSeconds2(Math.max(0, end - start)) + ' 秒';

    // 两个滑块重叠时保证“起点”可拖：起点超过一半时置于上层
    var mid = max / 2;
    els.trimStartRange.style.zIndex = start > mid ? '3' : '2';
    els.trimEndRange.style.zIndex = start > mid ? '2' : '3';

    updatePreviewButton();
  }

  function stopPreview() {
    state.previewing = false;
    try {
      els.video.pause();
    } catch (err) {
      /* ignore */
    }
    updatePreviewButton();
  }

  function updatePreviewButton() {
    if (!els || !els.previewBtn) return;
    els.previewBtn.textContent = state.previewing ? '停止预览' : '预览区间';
    els.previewBtn.setAttribute('aria-pressed', state.previewing ? 'true' : 'false');
  }

  function bindTrim() {
    on(els.trimStartRange, 'input', function () {
      setTrimStart(parseFloat(els.trimStartRange.value));
    });
    on(els.trimEndRange, 'input', function () {
      setTrimEnd(parseFloat(els.trimEndRange.value));
    });

    on(els.trimStartInput, 'input', function () {
      var value = parseFloat(els.trimStartInput.value);
      if (!isFinite(value)) return;
      setTrimStart(value);
    });
    on(els.trimStartInput, 'change', function () {
      var value = parseFloat(els.trimStartInput.value);
      if (isFinite(value)) setTrimStart(value);
      els.trimStartInput.value = fmtSeconds2(state.trim.start);
    });

    on(els.trimEndInput, 'input', function () {
      var value = parseFloat(els.trimEndInput.value);
      if (!isFinite(value)) return;
      setTrimEnd(value);
    });
    on(els.trimEndInput, 'change', function () {
      var value = parseFloat(els.trimEndInput.value);
      if (isFinite(value)) setTrimEnd(value);
      els.trimEndInput.value = fmtSeconds2(state.trim.end);
    });

    on(els.setStart, 'click', function () {
      if (!state.upload) return;
      var time = Number(els.video.currentTime) || 0;
      setTrimStart(time);
      els.trimStartInput.classList.add('is-flash');
      window.setTimeout(function () {
        els.trimStartInput.classList.remove('is-flash');
      }, 350);
    });

    on(els.setEnd, 'click', function () {
      if (!state.upload) return;
      var time = Number(els.video.currentTime) || 0;
      setTrimEnd(time);
      els.trimEndInput.classList.add('is-flash');
      window.setTimeout(function () {
        els.trimEndInput.classList.remove('is-flash');
      }, 350);
    });

    on(els.previewBtn, 'click', function () {
      if (!state.upload) return;
      if (state.previewing) {
        stopPreview();
        return;
      }
      if (!(state.trim.end - state.trim.start >= MIN_CLIP)) {
        showToast('区间太短：开始与结束至少相差 0.1 秒。', 'error');
        return;
      }
      if (state.mode !== 'trim') setMode('trim');
      state.previewing = true;
      try {
        els.video.currentTime = state.trim.start;
      } catch (err) {
        /* ignore */
      }
      var played = els.video.play();
      if (played && typeof played.catch === 'function') {
        played.catch(function () {
          state.previewing = false;
          updatePreviewButton();
          showToast('无法播放该视频，浏览器可能不支持此编码。', 'error');
        });
      }
      updatePreviewButton();
    });
  }

  /* ---------- 播放控制条（框选模式） ---------- */

  function updateTransportTime() {
    if (!els || !els.transportTime) return;
    var total = state.duration > 0 ? state.duration : 0;
    els.transportTime.textContent = fmtClock(els.video.currentTime || 0) + ' / ' + fmtClock(total);
    if (els.seek && document.activeElement !== els.seek) {
      els.seek.max = String(Math.max(total, 0.01));
      els.seek.value = String(Math.min(els.video.currentTime || 0, Math.max(total, 0.01)));
    }
  }

  function bindTransport() {
    on(els.playBtn, 'click', function () {
      if (!state.upload) return;
      if (els.video.paused) {
        var played = els.video.play();
        if (played && typeof played.catch === 'function') {
          played.catch(function () {
            showToast('无法播放该视频，浏览器可能不支持此编码。', 'error');
          });
        }
      } else {
        els.video.pause();
      }
    });

    on(els.seek, 'input', function () {
      if (!state.upload) return;
      var value = parseFloat(els.seek.value);
      if (isFinite(value)) {
        try {
          els.video.currentTime = value;
        } catch (err) {
          /* ignore */
        }
      }
    });
  }

  /* ---------- 剪窗口范围 ---------- */

  function cropMax() {
    return { w: state.videoWidth, h: state.videoHeight };
  }

  function cropIsValid() {
    var max = cropMax();
    return !!(state.crop.has && max.w > 0 && max.h > 0);
  }

  function clearCrop() {
    state.crop.has = false;
    state.crop.x = 0;
    state.crop.y = 0;
    state.crop.w = 0;
    state.crop.h = 0;
    renderCrop();
  }

  function selectAllCrop() {
    var max = cropMax();
    if (!max.w || !max.h) {
      showToast('视频信息尚未就绪，请稍后再试。', 'error');
      return;
    }
    state.crop.has = true;
    state.crop.x = 0;
    state.crop.y = 0;
    state.crop.w = max.w;
    state.crop.h = max.h;
    renderCrop(true);
  }

  function renderCrop(forceInputs) {
    if (!els) return;
    if (!cropIsValid()) {
      state.crop.has = false;
      els.cropSelection.hidden = true;
      els.cropLayer.classList.add('is-dimmed');
      els.cropSize.textContent = '未选择';
      if (els.cropX) els.cropX.disabled = true;
      if (els.cropY) els.cropY.disabled = true;
      if (els.cropW) els.cropW.disabled = true;
      if (els.cropH) els.cropH.disabled = true;
      if (els.cropEmptyHint) els.cropEmptyHint.hidden = false;
      if (els.exportCrop) els.exportCrop.disabled = true;
      return;
    }

    var max = cropMax();
    var crop = state.crop;
    var percent = function (value, base) {
      return (clamp(value / base, 0, 1) * 100).toFixed(4) + '%';
    };

    els.cropSelection.hidden = false;
    els.cropLayer.classList.remove('is-dimmed');
    els.cropSelection.style.left = percent(crop.x, max.w);
    els.cropSelection.style.top = percent(crop.y, max.h);
    els.cropSelection.style.width = percent(crop.w, max.w);
    els.cropSelection.style.height = percent(crop.h, max.h);

    els.cropX.disabled = false;
    els.cropY.disabled = false;
    els.cropW.disabled = false;
    els.cropH.disabled = false;

    if (forceInputs || document.activeElement !== els.cropX) els.cropX.value = String(Math.round(crop.x));
    if (forceInputs || document.activeElement !== els.cropY) els.cropY.value = String(Math.round(crop.y));
    if (forceInputs || document.activeElement !== els.cropW) els.cropW.value = String(Math.round(crop.w));
    if (forceInputs || document.activeElement !== els.cropH) els.cropH.value = String(Math.round(crop.h));

    els.cropSize.textContent = Math.round(crop.w) + ' × ' + Math.round(crop.h) + ' px';
    if (els.cropEmptyHint) els.cropEmptyHint.hidden = true;
    els.exportCrop.disabled = state.exporting;
  }

  function finalizeCrop() {
    var max = cropMax();
    if (!cropIsValid() || !max.w || !max.h) return;
    var crop = state.crop;
    crop.x = clamp(Math.round(crop.x), 0, Math.max(0, max.w - 1));
    crop.y = clamp(Math.round(crop.y), 0, Math.max(0, max.h - 1));
    crop.w = clamp(Math.round(crop.w), 1, max.w - crop.x);
    crop.h = clamp(Math.round(crop.h), 1, max.h - crop.y);
    renderCrop(true);
  }

  function applyCropField(field, value) {
    var max = cropMax();
    if (!cropIsValid() || !max.w || !max.h) return;
    var crop = state.crop;
    var v = Math.round(Number(value));
    if (!isFinite(v)) return;
    if (field === 'x') {
      crop.x = clamp(v, 0, Math.max(0, max.w - 1));
    } else if (field === 'y') {
      crop.y = clamp(v, 0, Math.max(0, max.h - 1));
    } else if (field === 'w') {
      crop.w = clamp(v, 1, Math.max(1, max.w - crop.x));
    } else if (field === 'h') {
      crop.h = clamp(v, 1, Math.max(1, max.h - crop.y));
    }
  }

  function bindCropInputs() {
    var fields = [
      [els.cropX, 'x'],
      [els.cropY, 'y'],
      [els.cropW, 'w'],
      [els.cropH, 'h']
    ];
    fields.forEach(function (pair) {
      var input = pair[0];
      var field = pair[1];
      on(input, 'input', function () {
        if (!cropIsValid()) return;
        var value = parseFloat(input.value);
        if (!isFinite(value)) return;
        applyCropField(field, value);
        renderCrop();
      });
      on(input, 'change', function () {
        if (!cropIsValid()) return;
        var value = parseFloat(input.value);
        if (isFinite(value)) applyCropField(field, value);
        renderCrop(true);
      });
    });
  }

  function bindCropButtons() {
    on(els.selectAll, 'click', selectAllCrop);
    on(els.resetCrop, 'click', function () {
      clearCrop();
    });
  }

  function bindCropDrag() {
    var layer = els.cropLayer;
    var drag = null;

    function videoRect() {
      return els.video.getBoundingClientRect();
    }

    function toVideoCoords(ev) {
      var rect = videoRect();
      var max = cropMax();
      if (!rect.width || !rect.height || !max.w || !max.h) return null;
      return {
        x: Math.round(clamp((ev.clientX - rect.left) / rect.width, 0, 1) * max.w),
        y: Math.round(clamp((ev.clientY - rect.top) / rect.height, 0, 1) * max.h)
      };
    }

    function snapshot() {
      return { x: state.crop.x, y: state.crop.y, w: state.crop.w, h: state.crop.h };
    }

    function fixedCorner(handle, snap) {
      if (handle === 'nw') return { x: snap.x + snap.w, y: snap.y + snap.h };
      if (handle === 'ne') return { x: snap.x, y: snap.y + snap.h };
      if (handle === 'sw') return { x: snap.x + snap.w, y: snap.y };
      return { x: snap.x, y: snap.y };
    }

    function applyDraw(anchor, point) {
      state.crop.has = true;
      state.crop.x = Math.min(anchor.x, point.x);
      state.crop.y = Math.min(anchor.y, point.y);
      state.crop.w = Math.abs(point.x - anchor.x);
      state.crop.h = Math.abs(point.y - anchor.y);
      renderCrop();
    }

    function applyMove(snap, delta, max) {
      state.crop.x = clamp(snap.x + delta.x, 0, Math.max(0, max.w - snap.w));
      state.crop.y = clamp(snap.y + delta.y, 0, Math.max(0, max.h - snap.h));
      renderCrop();
    }

    function applyResize(handle, snap, point) {
      var fixed = fixedCorner(handle, snap);
      state.crop.has = true;
      state.crop.x = Math.min(fixed.x, point.x);
      state.crop.y = Math.min(fixed.y, point.y);
      state.crop.w = Math.abs(point.x - fixed.x);
      state.crop.h = Math.abs(point.y - fixed.y);
      renderCrop();
    }

    on(layer, 'pointerdown', function (ev) {
      if (state.mode !== 'crop' || !state.upload) return;
      if (typeof ev.button === 'number' && ev.button !== 0) return;
      var max = cropMax();
      if (!max.w || !max.h) {
        showToast('视频信息尚未就绪，请稍后再试。', 'error');
        return;
      }
      var point = toVideoCoords(ev);
      if (!point) return;
      ev.preventDefault();

      var handle = ev.target && ev.target.getAttribute ? ev.target.getAttribute('data-handle') : null;
      if (handle) {
        drag = { mode: 'resize', handle: handle, snap: snapshot() };
      } else if (cropIsValid() && ev.target === els.cropSelection) {
        drag = {
          mode: 'move',
          snap: snapshot(),
          start: point
        };
      } else {
        drag = { mode: 'draw', anchor: point, snap: snapshot(), moved: false };
        state.crop.has = true;
        state.crop.x = point.x;
        state.crop.y = point.y;
        state.crop.w = 0;
        state.crop.h = 0;
        renderCrop();
      }

      try {
        layer.setPointerCapture(ev.pointerId);
      } catch (err) {
        /* 部分浏览器不支持指针捕获时仍可移动鼠标拖拽 */
      }
    });

    on(layer, 'pointermove', function (ev) {
      if (!drag || state.mode !== 'crop') return;
      var point = toVideoCoords(ev);
      if (!point) return;
      var max = cropMax();
      ev.preventDefault();
      if (drag.mode === 'draw') {
        drag.moved = true;
        applyDraw(drag.anchor, point);
      } else if (drag.mode === 'move') {
        applyMove(drag.snap, { x: point.x - drag.start.x, y: point.y - drag.start.y }, max);
      } else if (drag.mode === 'resize') {
        applyResize(drag.handle, drag.snap, point);
      }
    });

    function endDrag(ev) {
      if (!drag) return;
      var finished = drag;
      drag = null;
      try {
        if (layer.hasPointerCapture && layer.hasPointerCapture(ev.pointerId)) {
          layer.releasePointerCapture(ev.pointerId);
        }
      } catch (err) {
        /* ignore */
      }
      if (finished.mode === 'draw') {
        if (!finished.moved || state.crop.w < 3 || state.crop.h < 3) {
          // 单击（未拖动）视为重置选区
          clearCrop();
          return;
        }
      }
      finalizeCrop();
    }

    on(layer, 'pointerup', endDrag);
    on(layer, 'pointercancel', function (ev) {
      if (!drag) return;
      drag = null;
      finalizeCrop();
    });
  }

  function normalizeCropPayload() {
    var max = cropMax();
    if (!cropIsValid() || !max.w || !max.h) {
      showToast('请先在画面上框选区域。', 'error');
      return null;
    }
    var crop = state.crop;
    var x = Math.round(crop.x);
    var y = Math.round(crop.y);
    var width = Math.floor(Math.round(crop.w) / 2) * 2;
    var height = Math.floor(Math.round(crop.h) / 2) * 2;

    x = clamp(x, 0, Math.max(0, max.w - 1));
    y = clamp(y, 0, Math.max(0, max.h - 1));
    if (x + width > max.w) width = Math.floor((max.w - x) / 2) * 2;
    if (y + height > max.h) height = Math.floor((max.h - y) / 2) * 2;

    if (width < MIN_CROP || height < MIN_CROP) {
      showToast('选区太小：宽高至少 16 像素。', 'error');
      return null;
    }

    state.crop.x = x;
    state.crop.y = y;
    state.crop.w = width;
    state.crop.h = height;
    renderCrop(true);
    return { x: x, y: y, width: width, height: height };
  }

  /* ---------- 导出 ---------- */

  function bindExport() {
    on(els.exportTrim, 'click', function () {
      if (!state.upload || state.exporting) return;
      var start = round2(state.trim.start);
      var end = round2(state.trim.end);
      if (!(end - start >= MIN_CLIP)) {
        showToast('区间太短：开始与结束至少相差 0.1 秒。', 'error');
        return;
      }
      runExport('trim', { id: state.upload.id, start: start, end: end });
    });

    on(els.exportCrop, 'click', function () {
      if (!state.upload || state.exporting) return;
      var payload = normalizeCropPayload();
      if (!payload) return;
      payload.id = state.upload.id;
      runExport('crop', payload);
    });
  }

  function setExportingState(exporting) {
    state.exporting = exporting;
    els.exportTrim.disabled = exporting;
    if (exporting) {
      els.exportCrop.disabled = true;
      els.previewBtn.disabled = true;
    } else {
      els.previewBtn.disabled = false;
      renderCrop();
    }
  }

  async function runExport(kind, payload) {
    if (state.exporting) return;
    setExportingState(true);
    els.result.hidden = true;
    els.exportStatus.hidden = false;

    var startedAt = Date.now();
    function updateStatusText() {
      var seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
      els.exportStatusText.textContent = seconds > 0 ? '正在导出…已用时 ' + seconds + ' 秒' : '正在导出…';
    }
    updateStatusText();
    var timer = window.setInterval(updateStatusText, 500);

    var endpoint = kind === 'trim' ? 'api/trim' : 'api/crop';
    try {
      var data = await requestJSON(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!data || typeof data !== 'object' || !data.url) {
        throw makeHttpError('导出结果异常，请重试。', 502);
      }
      showResult(kind, payload, data);
      showToast('导出成功，可以下载了。', 'success');
    } catch (err) {
      if (!err || !err.isRedirect) {
        showToast((err && err.message) || '导出失败，请重试。', 'error');
      }
    } finally {
      window.clearInterval(timer);
      els.exportStatus.hidden = true;
      setExportingState(false);
    }
  }

  function showResult(kind, payload, data) {
    var title = kind === 'trim' ? '片段导出成功' : '窗口导出成功';
    var metaParts = [];
    if (kind === 'trim') {
      metaParts.push('区间 ' + fmtSeconds2(payload.start) + ' – ' + fmtSeconds2(payload.end) + ' 秒');
      if (isFinite(Number(data.duration))) metaParts.push('时长 ' + fmtSeconds2(Number(data.duration)) + ' 秒');
    } else {
      var dw = Number(data.width);
      var dh = Number(data.height);
      var dx = Number(data.x);
      var dy = Number(data.y);
      var cw = isFinite(dw) && dw > 0 ? dw : payload.width;
      var ch = isFinite(dh) && dh > 0 ? dh : payload.height;
      var cx = isFinite(dx) ? dx : payload.x;
      var cy = isFinite(dy) ? dy : payload.y;
      metaParts.push('区域 ' + cw + ' × ' + ch + ' px（x=' + cx + ', y=' + cy + '）');
    }
    if (isFinite(Number(data.size)) && Number(data.size) > 0) metaParts.push(fmtSize(Number(data.size)));

    els.resultTitle.textContent = title;
    els.resultMeta.textContent = metaParts.join(' · ');

    var downloadName = data.download_name || (kind === 'trim' ? 'supercut-clip.mp4' : 'supercut-crop.mp4');
    els.resultDownload.href = apiUrl(data.url);
    els.resultDownload.setAttribute('download', downloadName);
    els.resultDownload.textContent = '下载 ' + downloadName;
    els.result.hidden = false;
  }

  /* ---------- DOM ready ---------- */

  document.addEventListener('DOMContentLoaded', function () {
    initThemeSwitch();
    initLogin();
    initApp();
  });
})();
