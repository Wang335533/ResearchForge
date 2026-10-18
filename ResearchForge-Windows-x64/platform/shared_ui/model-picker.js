/* Shared conversation controls. Only public model metadata enters browser storage. */
window.RFModelPicker = class {
  constructor(root, key, onChange = () => {}) {
    this.root = root; this.key = key; this.onChange = onChange;
    this.selection = {}; this.valid = false;
    try { const saved = JSON.parse(localStorage.getItem(key) || '{}'); if(saved && typeof saved === 'object' && !Array.isArray(saved))this.selection = saved; } catch {}
    this.loadSequence = 0;
    root.classList.add('rf-model-picker');
    this.modelButton = this.button('正在读取模型…', () => this.open('model'));
    this.effortButton = this.button('思考强度', () => this.open('effort'));
    this.modelButton.disabled = this.effortButton.disabled = true;
    this.notice = document.createElement('span'); this.notice.className = 'rf-model-notice'; this.notice.setAttribute('role','status');
    root.append(this.modelButton, this.effortButton, this.notice);
    this.popup = document.createElement('div');
    this.popup.className = 'rf-model-menu'; this.popup.setAttribute('popover', 'auto');
    this.popup.id = key + '-menu'; this.popup.setAttribute('role', 'menu');
    document.body.append(this.popup);
    for (const button of [this.modelButton, this.effortButton]) {
      button.setAttribute('aria-haspopup', 'menu'); button.setAttribute('aria-controls', this.popup.id);
      button.setAttribute('aria-expanded', 'false');
    }
    this.popup.addEventListener('toggle', e => {
      this.modelButton.setAttribute('aria-expanded', String(e.newState === 'open' && this.mode === 'model'));
      this.effortButton.setAttribute('aria-expanded', String(e.newState === 'open' && this.mode === 'effort'));
    });
    this.popup.addEventListener('keydown', e => {
      const buttons = [...this.popup.querySelectorAll('button:not(:disabled)')];
      let i = buttons.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') i = (i + 1) % buttons.length;
      else if (e.key === 'ArrowUp') i = (i - 1 + buttons.length) % buttons.length;
      else if (e.key === 'Home') i = 0;
      else if (e.key === 'End') i = buttons.length - 1;
      else if (e.key === 'Escape') { this.popup.hidePopover(); this.anchor.focus(); return; }
      else if (e.key === 'Tab') { this.popup.hidePopover(); return; }
      else return;
      e.preventDefault(); buttons[i]?.focus();
    });
    this.ready = this.load();
  }
  button(label, action) {
    const button = document.createElement('button'); button.type = 'button';
    button.textContent = label; button.addEventListener('click', action); return button;
  }
  async load(refresh = false) {
    const sequence = ++this.loadSequence;
    this.controller?.abort(); this.controller = new AbortController();
    const controller = this.controller, timer = setTimeout(() => controller.abort(), 15000);
    this.valid = false;
    this.modelButton.disabled = this.effortButton.disabled = true;
    this.root.setAttribute('aria-busy', 'true'); this.notice.textContent = ''; this.onChange();
    try {
      const base = document.querySelector('meta[name="app-base"]');
      const endpoint = (base ? base.content : '/papers') + '/api/chatgpt/models';
      const response = await fetch(endpoint + (refresh ? '?refresh=1' : ''), {signal:controller.signal});
      const data = await response.json(); if (!response.ok) throw new Error(data.error || '模型目录读取失败');
      if (sequence !== this.loadSequence) return;
      if (!Array.isArray(data.models) || !data.models.length) throw new Error('当前账户没有可选模型');
      this.catalog = data;
      this.set(this.selection.model ? this.selection : data.defaults);
      this.modelButton.title = '选择本次对话模型'; this.effortButton.title = '选择本次对话思考强度';
    } catch (error) {
      if (sequence !== this.loadSequence) return;
      this.valid = false; this.modelButton.textContent = '重试读取模型';
      this.notice.textContent = error.name === 'AbortError' ? '读取超时，点击重试' : error.message;
      this.modelButton.title = this.notice.textContent; this.effortButton.disabled = true;
      this.onChange();
    } finally {
      clearTimeout(timer);
      if (sequence === this.loadSequence) {this.root.setAttribute('aria-busy', 'false'); this.modelButton.disabled = false;}
    }
  }
  set(choice) {
    if (!this.catalog) { this.selection = choice || {}; return; }
    const model = this.catalog.models.find(m => m.slug === choice?.model);
    this.selection = choice || {}; this.valid = false;
    if (!model) {
      this.modelButton.textContent = (choice?.model || '选择模型') + ' ⌄';
      this.effortButton.textContent = '思考强度'; this.effortButton.disabled = true;
      this.notice.textContent = '原模型在当前账户不可用，请重新选择。'; this.onChange(); return;
    }
    const effort = model.reasoning_levels.some(x => x.enabled && x.effort === choice?.reasoning_effort)
      ? choice.reasoning_effort : model.default_reasoning_effort;
    this.selection = {model: model.slug, reasoning_effort: effort}; this.valid = true;
    this.modelButton.textContent = model.display_name + ' ⌄';
    this.effortButton.textContent = this.label(effort) + ' ⌄'; this.effortButton.disabled = false;
    this.notice.textContent = this.catalog.verified ? '' : this.catalog.message;
    try { localStorage.setItem(this.key, JSON.stringify(this.selection)); } catch {}
    this.onChange();
  }
  label(effort) { return {none:'None',minimal:'Minimal',low:'Low',medium:'Medium',high:'High',xhigh:'Extra High',max:'Max',ultra:'Ultra'}[effort] || '默认'; }
  value() { if (!this.valid) throw new Error('请先成功读取模型列表。'); return {...this.selection}; }
  open(mode) {
    if (!this.catalog || (!this.valid && this.modelButton.textContent === '重试读取模型')) { this.load(true); return; }
    if(mode === 'effort' && !this.valid)return;
    if (this.popup.matches(':popover-open') && this.mode === mode) { this.popup.hidePopover(); return; }
    this.mode = mode; this.anchor = mode === 'model' ? this.modelButton : this.effortButton;
    this.popup.replaceChildren(); this.popup.setAttribute('aria-label', mode === 'model' ? '选择模型' : '选择思考强度');
    const heading = document.createElement('div'); heading.className = 'rf-model-heading';
    heading.textContent = mode === 'model' ? '模型' : '思考强度'; this.popup.append(heading);
    const model = this.catalog.models.find(m => m.slug === this.selection.model);
    const rows = mode === 'model' ? this.catalog.models.map(m => ({value:m.slug,label:m.display_name,enabled:true})) :
      model.reasoning_levels.map(x => ({value:x.effort,label:this.label(x.effort),enabled:x.enabled}));
    for (const row of rows) {
      const current = row.value === (mode === 'model' ? this.selection.model : this.selection.reasoning_effort);
      const button = this.button(row.label, () => {
        this.set(mode === 'model' ? {...this.selection,model:row.value} : {...this.selection,reasoning_effort:row.value});
        this.popup.hidePopover(); this.anchor.focus();
      });
      button.className = 'rf-model-option'; button.disabled = !row.enabled;
      button.setAttribute('role', 'menuitemradio'); button.setAttribute('aria-checked', String(current));
      if (!row.enabled) { const note = document.createElement('small'); note.textContent = '此接口暂不支持'; button.append(note); }
      const check = document.createElement('span'); check.textContent = current ? '✓' : ''; check.setAttribute('aria-hidden','true'); button.append(check);
      this.popup.append(button);
    }
    const footer = document.createElement('div'); footer.className = 'rf-model-menu-footer';
    footer.append(this.button('恢复默认', () => {this.set(this.catalog.defaults);this.popup.hidePopover();this.anchor.focus();}));
    if (mode === 'model') footer.append(this.button('刷新列表', () => {this.popup.hidePopover();this.load(true);}));
    this.popup.append(footer);
    const rect = this.anchor.getBoundingClientRect(), gap = 8;
    const above = rect.top >= 180;
    this.popup.style.maxHeight = Math.max(120, (above ? rect.top : innerHeight - rect.bottom) - gap * 2) + 'px';
    this.popup.showPopover();
    const height = this.popup.offsetHeight;
    this.popup.style.left = Math.max(gap, Math.min(rect.left, innerWidth - this.popup.offsetWidth - gap)) + 'px';
    this.popup.style.top = (above ? Math.max(gap, rect.top - height - gap) : rect.bottom + gap) + 'px';
    (this.popup.querySelector('[aria-checked="true"]') || this.popup.querySelector('button:not(:disabled)'))?.focus({preventScroll:true});
  }
};
