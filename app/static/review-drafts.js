/* Source-bound partial progress. Formal review submission stays explicit. */
class ReviewDraftAutosave {
  constructor(options) {
    this.options = options;
    this.revision = 0;
    this.source = null;
    this.fields = {};
    this.serial = 0;
    this.dirty = false;
    this.closed = false;
    this.conflict = false;
    this.timer = null;
    this.inflight = null;
    try {
      this.tab = sessionStorage.getItem('poster-review-draft-tab');
      if (!this.tab) {
        this.tab = crypto.randomUUID();
        sessionStorage.setItem('poster-review-draft-tab', this.tab);
      }
    } catch { this.tab = crypto.randomUUID(); }
    this.onInput = () => this.changed();
    this.onSave = () => this.flush();
    this.onReload = () => this.reload();
    this.onLeave = () => { if (this.dirty) this.flush(true); };
    this.onOnline = () => { if (this.dirty && !this.conflict) this.flush(); };
    this.onUnload = event => {
      if (this.dirty && !this.closed) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    options.saveButton.addEventListener('click', this.onSave);
    options.reloadButton.addEventListener('click', this.onReload);
    window.addEventListener('pagehide', this.onLeave);
    window.addEventListener('online', this.onOnline);
    window.addEventListener('beforeunload', this.onUnload);
  }

  message(text, problem = false) {
    if (this.closed) return;
    this.options.status.textContent = text;
    this.options.status.className = 'tip ' + (problem ? 'bad' : '');
  }

  controls(disabled) {
    if (this.closed) return;
    for (const node of this.options.container.querySelectorAll('select,input')) node.disabled = disabled;
  }

  backup() {
    if (!this.source) return;
    try {
      localStorage.setItem(this.backupKey, JSON.stringify({
        source_sha256: this.source, expected_revision: this.revision, fields: this.fields,
      }));
      this.hasBackup = true;
    } catch { this.hasBackup = false; }
  }

  clearBackup() {
    if (!this.backupKey) return;
    try { localStorage.removeItem(this.backupKey); } catch { /* Server copy remains durable. */ }
  }

  async start({restoreBackup = true} = {}) {
    this.controls(true);
    this.options.saveButton.disabled = true;
    this.options.reloadButton.disabled = true;
    this.message('正在读取已保存的审核进度…');
    try {
      const response = await fetch(this.options.url, {cache: 'no-store'});
      const data = await response.json();
      if (this.closed) return false;
      if (!response.ok) throw Error(this.options.errorText(data));
      const baseId = this.options.context.existing_review?.review_id || null;
      if (data.image_sha256 !== this.options.context.image_sha256 ||
          data.spec_sha256 !== this.options.context.spec_sha256 || data.base_review_id !== baseId) {
        throw Error('图片或正式审核已更新，请重新打开海报后继续。');
      }
      this.source = data.source_sha256;
      this.revision = data.revision;
      this.backupKey = 'poster-review-progress:' + this.tab + ':' + this.source;
      if (!restoreBackup) {
        this.clearBackup();
        this.dirty = false;
        this.conflict = false;
      }
      this.options.apply(data.fields);
      this.fields = this.options.read();
      let pending = null;
      try { if (restoreBackup) pending = JSON.parse(localStorage.getItem(this.backupKey) || 'null'); } catch { /* No usable backup. */ }
      if (pending?.source_sha256 === this.source && pending.fields &&
          Number.isInteger(pending.expected_revision) && pending.expected_revision >= 0) {
        // Only fields recognized by this actual image are restored into controls.
        const differs = JSON.stringify(pending.fields) !== JSON.stringify(this.fields);
        if (differs) {
          this.options.apply(pending.fields);
          this.fields = this.options.read();
          this.dirty = true;
          this.serial++;
          this.conflict = pending.expected_revision !== data.revision;
          this.revision = pending.expected_revision;
          this.backup();
        } else this.clearBackup();
      }
      this.options.container.addEventListener('input', this.onInput);
      this.options.container.addEventListener('change', this.onInput);
      this.options.saveButton.disabled = false;
      this.options.reloadButton.disabled = false;
      this.controls(false);
      if (this.conflict) this.message('此页面的未保存输入已恢复；另一个页面更新了进度。可载入已保存版本，当前输入不会自动覆盖它。', true);
      else if (this.dirty) {
        this.message('已恢复本页尚未保存的输入，正在同步到本机…');
        this.flush();
      } else this.message(data.revision
        ? '已恢复本机保存的进度 · 版本 ' + data.revision + '；尚未提交审核。'
        : '修改后自动保存进度；点击提交才会形成正式审核记录。');
      return true;
    } catch (error) {
      this.message('无法载入审核进度：' + error.message + ' 可点击「载入已保存进度」重试。', true);
      if (!this.closed) this.options.reloadButton.disabled = false;
      return false;
    }
  }

  changed() {
    if (this.closed || !this.source) return;
    const latest = this.options.read();
    if (JSON.stringify(latest) === JSON.stringify(this.fields)) return;
    this.fields = latest;
    this.dirty = true;
    this.serial++;
    this.backup();
    if (this.conflict) return;
    this.message('有新修改，正在自动保存…');
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.flush(), 700);
  }

  async flush(keepalive = false) {
    clearTimeout(this.timer);
    if (this.inflight) {
      const finished = await this.inflight;
      if (!finished) return false;
      return this.dirty ? this.flush(keepalive) : true;
    }
    if (!this.dirty) return true;
    if (!this.source || this.conflict) return false;
    const serial = this.serial;
    const body = JSON.stringify({source_sha256: this.source,
      expected_revision: this.revision, fields: this.fields});
    this.inflight = (async () => {
      try {
        const response = await fetch(this.options.url, {
          method: 'PUT', headers: {'Content-Type': 'application/json'}, body, keepalive,
        });
        const data = await response.json();
        if (!response.ok) {
          if (response.status === 409) this.conflict = true;
          throw Error(this.options.errorText(data));
        }
        this.revision = data.revision;
        if (serial === this.serial) {
          this.dirty = false;
          this.clearBackup();
          this.message('进度已保存于本机 · 版本 ' + data.revision + '；尚未提交审核。');
        } else this.backup();
        return true;
      } catch (error) {
        this.message((this.hasBackup ? '本页输入已临时备份；' : '进度尚未保存；') +
          error.message + '。请重试保存或载入已保存进度。', true);
        return false;
      }
    })();
    const result = await this.inflight;
    this.inflight = null;
    if (result && this.dirty) return this.flush(keepalive);
    return result && !this.dirty;
  }

  async reload() {
    if (this.dirty && !window.confirm('载入已保存进度会替换当前尚未保存的输入，是否继续？')) return;
    this.options.container.removeEventListener('input', this.onInput);
    this.options.container.removeEventListener('change', this.onInput);
    if (this.inflight) await this.inflight;
    this.inflight = null;
    await this.start({restoreBackup: false});
  }

  async beginSubmit() {
    this.changed();
    this.controls(true);
    const saved = await this.flush();
    if (!saved) this.controls(false);
    return saved && !this.closed;
  }

  close() {
    clearTimeout(this.timer);
    if (this.dirty && this.source && !this.conflict) this.flush(true);
    this.closed = true;
    this.options.container.removeEventListener('input', this.onInput);
    this.options.container.removeEventListener('change', this.onInput);
    this.options.saveButton.removeEventListener('click', this.onSave);
    this.options.reloadButton.removeEventListener('click', this.onReload);
    window.removeEventListener('pagehide', this.onLeave);
    window.removeEventListener('online', this.onOnline);
    window.removeEventListener('beforeunload', this.onUnload);
  }
}
window.ReviewDraftAutosave = ReviewDraftAutosave;
