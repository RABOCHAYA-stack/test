'use strict';

/* ---------- Состояние и утилиты ---------- */

const state = {
  user: null,
  meta: null,
  appeals: [],
  filters: {
    q: '',
    type: '',
    category: '',
    status: '',
    priority: '',
    department: '',
    mine: false,
    overdue: false,
  },
  timer: null,
};

const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));

const STATUS_CLASS = {
  'Новое': 'status-new',
  'На рассмотрении': 'status-review',
  'В работе': 'status-work',
  'Ожидает уточнения': 'status-wait',
  'Реализовано': 'status-done',
  'Отклонено': 'status-rejected',
  'Закрыто': 'status-closed',
};
const TYPE_CLASS = {
  'Проблема': 'type-problem',
  'Предложение': 'type-idea',
  'Положительный отзыв': 'type-praise',
};
const PRIO_CLASS = { 'Высокий': 'prio-high', 'Средний': 'prio-medium', 'Низкий': 'prio-low' };
const ROLE_LABEL = { employee: 'Сотрудник', manager: 'Руководитель', admin: 'Администратор' };
const FINAL = ['Реализовано', 'Отклонено', 'Закрыто'];

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function fmtDate(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
    + ', ' + date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

function daysBetween(a, b) {
  return (new Date(a) - new Date(b)) / 86400000;
}

function plural(n, one, few, many) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function initials(name) {
  return String(name || '?').trim().split(/\s+/).slice(0, 2).map((p) => p[0] || '').join('').toUpperCase();
}

async function api(path, options = {}) {
  const opts = { credentials: 'same-origin', ...options };
  if (opts.body && typeof opts.body !== 'string') {
    opts.headers = { ...(opts.headers || {}), 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(opts.body);
  }
  const response = await fetch(path, opts);
  const type = response.headers.get('content-type') || '';
  const data = type.includes('application/json') ? await response.json().catch(() => null) : null;
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/api/login')) showLogin();
    const error = new Error((data && data.error) || `Ошибка ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

let toastTimer = null;
function toast(message, kind = '') {
  const node = $('#toast');
  node.textContent = message;
  node.className = 'toast show ' + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = 'toast'; }, 4200);
}

function setTitle(text) {
  $('#page-title').textContent = text;
  document.title = text + ' · Модус Обратная связь';
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = () => reject(new Error('Не удалось прочитать файл'));
    reader.readAsDataURL(file);
  });
}

/* ---------- Вход ---------- */

function showLogin() {
  state.user = null;
  state.meta = null;
  $('#app').classList.add('hidden');
  $('#login').classList.remove('hidden');
  $('#login-error').classList.add('hidden');
}

function showApp() {
  $('#login').classList.add('hidden');
  $('#app').classList.remove('hidden');
  renderUser();
  renderNav();
}

function renderUser() {
  const user = state.user;
  if (!user) return;
  $('#user-avatar').textContent = initials(user.name);
  $('#user-name').textContent = user.name;
  $('#user-role').textContent = user.position || ROLE_LABEL[user.role];
  $('#role-badge').textContent = ROLE_LABEL[user.role] || user.role;
}

function renderNav() {
  const role = state.user ? state.user.role : '';
  $$('#nav .nav-item').forEach((link) => {
    const roles = (link.dataset.roles || '').split(' ').filter(Boolean);
    link.classList.toggle('hidden', roles.length > 0 && !roles.includes(role));
  });
}

async function loadMeta() {
  state.meta = await api('/api/meta');
  state.user = state.meta.user;
}

async function doLogin(login, password) {
  const button = $('#login-form button[type="submit"]');
  const errorBox = $('#login-error');
  errorBox.classList.add('hidden');
  button.disabled = true;
  try {
    const data = await api('/api/login', { method: 'POST', body: { login, password } });
    state.user = data.user;
    await loadMeta();
    showApp();
    location.hash = '#/appeals';
    route();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove('hidden');
  } finally {
    button.disabled = false;
  }
}

/* ---------- Роутинг ---------- */

function route() {
  if (!state.user) return;
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  const page = parts[0] || 'appeals';

  if (page === 'dashboard' && state.user.role === 'employee') {
    location.hash = '#/appeals';
    return;
  }
  if (page === 'admin' && state.user.role !== 'admin') {
    location.hash = '#/appeals';
    return;
  }

  $$('#nav .nav-item').forEach((link) => link.classList.remove('active'));
  if (page === 'appeals' && parts[1] === 'new') {
    const nav = $('#nav [data-route="new"]');
    if (nav) nav.classList.add('active');
    setTitle('Новое обращение');
    renderNew();
  } else if (page === 'appeal' && parts[1]) {
    const nav = $('#nav [data-route="appeals"]');
    if (nav) nav.classList.add('active');
    setTitle('Обращение');
    renderDetail(Number(parts[1]));
  } else if (page === 'dashboard') {
    const nav = $('#nav [data-route="dashboard"]');
    if (nav) nav.classList.add('active');
    setTitle('Аналитика');
    renderDashboard();
  } else if (page === 'admin') {
    const nav = $('#nav [data-route="admin"]');
    if (nav) nav.classList.add('active');
    setTitle('Настройки');
    renderAdmin();
  } else {
    const nav = $('#nav [data-route="appeals"]');
    if (nav) nav.classList.add('active');
    setTitle('Обращения');
    renderList();
  }
}

/* ---------- Список обращений ---------- */

function optionList(items, current, placeholder) {
  return `<option value="">${esc(placeholder)}</option>` + items.map((item) => {
    const value = item.value !== undefined ? item.value : item;
    const label = item.label !== undefined ? item.label : item;
    return `<option value="${esc(value)}" ${String(current) === String(value) ? 'selected' : ''}>${esc(label)}</option>`;
  }).join('');
}

function statusBadge(status) {
  return `<span class="badge ${STATUS_CLASS[status] || 'badge-soft'}">${esc(status)}</span>`;
}

function typeBadge(type) {
  return `<span class="badge ${TYPE_CLASS[type] || 'badge-soft'}">${esc(type)}</span>`;
}

function priorityBadge(priority) {
  return `<span class="badge ${PRIO_CLASS[priority] || 'badge-soft'}">${esc(priority)}</span>`;
}

function dueText(appeal) {
  if (FINAL.includes(appeal.status)) {
    return appeal.closed_at ? `Закрыто ${fmtDate(appeal.closed_at)}` : 'Рассмотрено';
  }
  if (appeal.overdue) {
    const late = Math.max(1, Math.ceil(daysBetween(new Date(), appeal.due_at)));
    return `Просрочено на ${late} ${plural(late, 'день', 'дня', 'дней')}`;
  }
  const left = Math.max(0, Math.ceil(daysBetween(appeal.due_at, new Date())));
  return `Срок до ${fmtDate(appeal.due_at)} · ${left} ${plural(left, 'день', 'дня', 'дней')}`;
}

function appealRow(appeal) {
  return `
    <article class="appeal-row ${appeal.overdue ? 'overdue' : ''}" data-id="${appeal.id}" tabindex="0" role="button">
      <div class="appeal-main">
        <div class="appeal-top">
          <span class="code">${esc(appeal.code)}</span>
          ${typeBadge(appeal.type)}
          ${priorityBadge(appeal.priority)}
          ${appeal.anonymous ? '<span class="badge badge-outline">Анонимно</span>' : ''}
        </div>
        <div class="appeal-title">${esc(appeal.title)}</div>
        <div class="appeal-meta">
          <span>${esc(appeal.category || '—')}</span>
          <span>${esc(appeal.author || '—')}</span>
          <span>${esc(appeal.author_department || '—')}</span>
          <span>${fmtDate(appeal.created_at)}</span>
          <span>${appeal.comments_count} ${plural(appeal.comments_count, 'комментарий', 'комментария', 'комментариев')}</span>
        </div>
      </div>
      <div class="appeal-side">
        <div class="badges">${statusBadge(appeal.status)}</div>
        <div class="appeal-assignee">${appeal.assignee ? 'Ответственный: ' + esc(appeal.assignee) : 'Ответственный не назначен'}</div>
        <div class="appeal-due ${appeal.overdue ? 'late' : ''}">${dueText(appeal)}</div>
      </div>
    </article>`;
}

function renderList() {
  const meta = state.meta;
  const filters = state.filters;
  const categories = meta.categories.map((c) => ({ value: c.id, label: c.name }));
  const departments = meta.departments.map((d) => ({ value: d, label: d }));

  $('#view').innerHTML = `
    <div class="page-head">
      <div>
        <h2>Обращения сотрудников</h2>
        <div class="sub">Проблемы, предложения и отзывы — с ответственным, сроком и результатом рассмотрения.</div>
      </div>
      <a class="btn btn-primary" href="#/appeals/new">+ Новое обращение</a>
    </div>

    <div class="card card-pad filters">
      <div class="filters-row">
        <div class="search-wrap">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.2-3.2"/></svg>
          <input id="f-q" type="search" placeholder="Поиск по номеру, теме или тексту" value="${esc(filters.q)}">
        </div>
        <select id="f-type">${optionList(meta.types, filters.type, 'Все типы')}</select>
        <select id="f-status">${optionList(meta.statuses, filters.status, 'Все статусы')}</select>
        <select id="f-category">${optionList(categories, filters.category, 'Все категории')}</select>
        <select id="f-priority">${optionList(meta.priorities, filters.priority, 'Любой приоритет')}</select>
      </div>
      <div class="filters-row second">
        <select id="f-department">${optionList(departments, filters.department, 'Все подразделения')}</select>
        <label class="check"><input id="f-mine" type="checkbox" ${filters.mine ? 'checked' : ''}> Только мои</label>
        <label class="check"><input id="f-overdue" type="checkbox" ${filters.overdue ? 'checked' : ''}> Только просроченные</label>
        <div></div>
        <button class="btn btn-ghost btn-sm" id="f-reset" type="button">Сбросить фильтры</button>
      </div>
    </div>

    <div id="list-count" class="muted small" style="margin: 0 2px 10px"></div>
    <div id="appeal-list" class="appeal-list"></div>`;

  const apply = () => {
    state.filters.q = $('#f-q').value.trim();
    state.filters.type = $('#f-type').value;
    state.filters.status = $('#f-status').value;
    state.filters.category = $('#f-category').value;
    state.filters.priority = $('#f-priority').value;
    state.filters.department = $('#f-department').value;
    state.filters.mine = $('#f-mine').checked;
    state.filters.overdue = $('#f-overdue').checked;
    fetchAppeals();
  };

  $('#f-q').addEventListener('input', () => {
    clearTimeout(state.timer);
    state.timer = setTimeout(apply, 300);
  });
  ['f-type', 'f-status', 'f-category', 'f-priority', 'f-department'].forEach((id) => {
    $('#' + id).addEventListener('change', apply);
  });
  ['f-mine', 'f-overdue'].forEach((id) => $('#' + id).addEventListener('change', apply));
  $('#f-reset').addEventListener('click', () => {
    state.filters = { q: '', type: '', category: '', status: '', priority: '', department: '', mine: false, overdue: false };
    renderList();
  });

  $('#appeal-list').addEventListener('click', (event) => {
    const row = event.target.closest('.appeal-row');
    if (row) location.hash = '#/appeal/' + row.dataset.id;
  });
  $('#appeal-list').addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const row = event.target.closest('.appeal-row');
    if (row) {
      event.preventDefault();
      location.hash = '#/appeal/' + row.dataset.id;
    }
  });

  fetchAppeals();
}

async function fetchAppeals() {
  const list = $('#appeal-list');
  if (!list) return;
  list.innerHTML = '<div class="empty">Загружаем обращения…</div>';
  const params = new URLSearchParams();
  Object.entries(state.filters).forEach(([key, value]) => {
    if (value) params.set(key, value === true ? '1' : value);
  });
  try {
    const data = await api('/api/appeals?' + params.toString());
    state.appeals = data.appeals;
    const count = data.appeals.length;
    $('#list-count').textContent = count
      ? `Найдено ${count} ${plural(count, 'обращение', 'обращения', 'обращений')}`
      : 'Ничего не найдено';
    list.innerHTML = count
      ? data.appeals.map(appealRow).join('')
      : `<div class="empty"><strong>Обращений нет</strong>Попробуйте изменить фильтры или создайте новое обращение.</div>`;
  } catch (error) {
    list.innerHTML = `<div class="empty"><strong>Не удалось загрузить</strong>${esc(error.message)}</div>`;
  }
}

/* ---------- Новое обращение ---------- */

function renderNew() {
  const meta = state.meta;
  const categories = meta.categories.filter((c) => c.active).map((c) => ({
    value: c.id,
    label: c.name + (c.responsible_name ? ` · ${c.responsible_name}` : ' · без ответственного'),
  }));

  $('#view').innerHTML = `
    <div class="page-head">
      <div>
        <h2>Новое обращение</h2>
        <div class="sub">Опишите проблему или предложение — обращение будет зарегистрировано и направлено ответственному.</div>
      </div>
    </div>

    <form id="new-form" class="card card-pad form-card" novalidate>
      <div class="field">
        <span>Тип обращения</span>
        <div class="chip-group" id="type-chips">
          ${meta.types.map((type, index) => `
            <button type="button" class="chip ${index === 0 ? 'active' : ''}" data-type="${esc(type)}">${esc(type)}</button>`).join('')}
        </div>
      </div>

      <div class="form-grid">
        <label class="field span-2">
          <span>Кратко о проблеме или предложении</span>
          <input id="f-title" type="text" maxlength="140" placeholder="Например: не хватает обязательных полей в заявке" required>
          <span class="hint">5–140 символов, чтобы обращение было легко найти в списке.</span>
        </label>

        <label class="field">
          <span>Категория</span>
          <select id="f-category" required>${optionList(categories, '', 'Выберите категорию')}</select>
        </label>

        <label class="field">
          <span>Приоритет</span>
          <select id="f-priority">${optionList(meta.priorities, 'Средний', 'Выберите приоритет')}</select>
          <span class="hint">Срок реакции: высокий — ${meta.sla_days['Высокий']} дн., средний — ${meta.sla_days['Средний']} дн., низкий — ${meta.sla_days['Низкий']} дн.</span>
        </label>

        <label class="field span-2">
          <span>Описание</span>
          <textarea id="f-desc" maxlength="5000" placeholder="Что происходит, где и как часто? Что мешает в работе?" required></textarea>
          <span class="hint">От 15 символов. Не указывайте персональные данные клиентов и коммерческие условия.</span>
        </label>

        <label class="field span-2">
          <span>Предлагаемое решение <span class="muted">(необязательно)</span></span>
          <textarea id="f-expected" maxlength="3000" placeholder="Как, по вашему мнению, можно улучшить ситуацию?"></textarea>
        </label>

        <div class="field span-2">
          <label class="check">
            <input id="f-anon" type="checkbox">
            <span>Отправить анонимно. Автор не будет виден руководителям и в ленте; администратор системы видит автора для контроля злоупотреблений.</span>
          </label>
        </div>

        <div class="field span-2">
          <span>Вложение <span class="muted">(необязательно, до 2 МБ)</span></span>
          <div class="file-box">
            <span class="name" id="file-name">PDF, DOC, XLS, PNG, JPG или TXT</span>
            <input id="f-file" type="file" accept=".pdf,.doc,.docx,.xls,.xlsx,.png,.jpg,.jpeg,.txt" style="max-width: 260px">
          </div>
        </div>
      </div>

      <p id="new-error" class="form-error hidden"></p>

      <div class="form-actions">
        <button class="btn btn-ghost" type="button" id="cancel-new">Отмена</button>
        <button class="btn btn-primary" type="submit">Отправить обращение</button>
      </div>
    </form>`;

  let selectedType = meta.types[0];
  $$('#type-chips .chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      $$('#type-chips .chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      selectedType = chip.dataset.type;
    });
  });

  $('#f-file').addEventListener('change', () => {
    const file = $('#f-file').files[0];
    $('#file-name').textContent = file ? `${file.name} · ${(file.size / 1024).toFixed(0)} КБ` : 'Файл не выбран';
  });

  $('#cancel-new').addEventListener('click', () => { history.back(); });

  $('#new-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const errorBox = $('#new-error');
    errorBox.classList.add('hidden');
    const submit = $('#new-form button[type="submit"]');
    const file = $('#f-file').files[0];
    if (file && file.size > 2 * 1024 * 1024) {
      errorBox.textContent = 'Файл больше 2 МБ';
      errorBox.classList.remove('hidden');
      return;
    }
    const body = {
      type: selectedType,
      title: $('#f-title').value.trim(),
      category_id: Number($('#f-category').value) || null,
      priority: $('#f-priority').value,
      description: $('#f-desc').value.trim(),
      expected_solution: $('#f-expected').value.trim(),
      anonymous: $('#f-anon').checked,
    };
    if (!body.category_id) {
      errorBox.textContent = 'Выберите категорию';
      errorBox.classList.remove('hidden');
      return;
    }
    submit.disabled = true;
    try {
      if (file) {
        body.attachment = { filename: file.name, data: await fileToBase64(file) };
      }
      const result = await api('/api/appeals', { method: 'POST', body });
      toast(`Обращение ${result.code} зарегистрировано. Ответственный: ${result.assignee}`, 'success');
      location.hash = '#/appeal/' + result.id;
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.classList.remove('hidden');
      submit.disabled = false;
    }
  });
}

/* ---------- Детальная карточка ---------- */

function timelineItem(item) {
  const isComment = item.kind === 'comment';
  const dotClass = isComment
    ? (item.author_role === 'employee' ? 'author' : 'manager')
    : (item.actor ? 'manager' : 'system');
  const dotText = isComment || item.actor ? initials(isComment ? item.author : item.actor) : '·';
  const head = isComment
    ? `<b>${esc(item.author)}</b><span>${fmtDateTime(item.created_at)}</span>${item.internal ? '<span class="internal-flag">внутренний</span>' : ''}`
    : `<b>${esc(item.actor || 'Система')}</b><span>${fmtDateTime(item.created_at)}</span>`;
  const text = isComment
    ? `<div class="timeline-text">${esc(item.text)}</div>`
    : `<div class="timeline-event">${esc(item.details)}</div>`;
  return `
    <div class="timeline-item">
      <div class="dot ${dotClass}">${esc(dotText)}</div>
      <div class="timeline-body">
        <div class="timeline-head">${head}</div>
        ${text}
      </div>
    </div>`;
}

async function renderDetail(id) {
  $('#view').innerHTML = '<div class="empty">Загружаем обращение…</div>';
  let data;
  try {
    data = await api('/api/appeals/' + id);
  } catch (error) {
    $('#view').innerHTML = `<div class="empty"><strong>Обращение недоступно</strong>${esc(error.message)}</div>`;
    return;
  }
  const appeal = data.appeal;
  setTitle(`${appeal.code} · ${appeal.title}`);

  const items = [
    ...data.events.map((e) => ({ kind: 'event', ...e })),
    ...data.comments.map((c) => ({ kind: 'comment', ...c })),
  ].sort((a, b) => new Date(a.created_at) - new Date(b.created_at));

  const managers = state.meta.users.filter((u) => u.role !== 'employee');
  const assignOptions = managers.map((u) => ({
    value: u.id,
    label: `${u.name} — ${u.position || u.department}`,
  }));

  const canManage = appeal.can_manage;
  const canTake = appeal.can_take;
  const statusOptions = state.meta.statuses.filter((s) => s !== appeal.status);

  const actionPanel = (canManage || canTake) ? `
    <div class="card card-pad actions">
      <div class="section-title">Действия ответственного</div>
      ${canTake ? '<button class="btn btn-primary" id="take-btn" type="button" style="margin-bottom:14px">Взять обращение в работу</button>' : ''}
      ${canManage ? `
        <form id="status-form">
          <label class="field">
            <span>Новый статус</span>
            <select id="a-status">${optionList(statusOptions, '', 'Выберите статус')}</select>
          </label>
          <label class="field">
            <span>Результат рассмотрения / причина</span>
            <textarea id="a-resolution" maxlength="3000" style="min-height:84px" placeholder="Что сделано или почему обращение отклонено">${esc(appeal.resolution || '')}</textarea>
            <span class="hint">Обязательно для статусов «Реализовано», «Отклонено», «Закрыто».</span>
          </label>
          <label class="field">
            <span>Комментарий <span class="muted">(необязательно)</span></span>
            <textarea id="a-comment" maxlength="3000" style="min-height:64px" placeholder="Промежуточный комментарий автору"></textarea>
          </label>
          <label class="check" style="margin-bottom:12px">
            <input id="a-internal" type="checkbox"> Внутренний комментарий (не виден автору)
          </label>
          <button class="btn btn-primary" type="submit">Сохранить статус</button>
        </form>
        <div class="divider"></div>
        <div class="field">
          <span>Ответственный</span>
          <select id="a-assignee">${optionList(assignOptions, appeal.assignee_id || '', 'Выберите сотрудника')}</select>
        </div>
        <button class="btn btn-ghost" id="assign-btn" type="button">Переназначить</button>
      ` : `<div class="actions-note">Статус и результат меняет ответственный: ${esc(appeal.assignee || 'не назначен')}.</div>`}
    </div>` : `
    <div class="card card-pad actions">
      <div class="actions-note">Изменять статус и результат может ответственный по обращению${appeal.assignee ? ': ' + esc(appeal.assignee) : ''}.</div>
    </div>`;

  const commentForm = appeal.can_comment ? `
    <form id="comment-form" class="comment-form">
      <div class="section-title">Добавить комментарий</div>
      <textarea id="c-text" maxlength="3000" placeholder="Уточнение, вопрос или дополнительная информация"></textarea>
      ${state.user.role !== 'employee' ? `
        <label class="check"><input id="c-internal" type="checkbox"> Внутренний комментарий (не виден автору)</label>` : ''}
      <div style="display:flex;justify-content:flex-end"><button class="btn btn-ghost btn-sm" type="submit">Отправить</button></div>
    </form>` : '';

  $('#view').innerHTML = `
    <a class="back-link" href="#/appeals">← К списку обращений</a>

    <div class="detail-head">
      <div class="detail-badges">
        <span class="code">${esc(appeal.code)}</span>
        ${typeBadge(appeal.type)}
        ${statusBadge(appeal.status)}
        ${priorityBadge(appeal.priority)}
        ${appeal.anonymous ? '<span class="badge badge-outline">Анонимно</span>' : ''}
        ${appeal.overdue ? '<span class="badge status-rejected">Просрочено</span>' : ''}
      </div>
      <h2>${esc(appeal.title)}</h2>
      <div class="detail-meta">
        <span>Создано: <b>${fmtDateTime(appeal.created_at)}</b></span>
        <span>Срок: <b>${fmtDate(appeal.due_at)}</b></span>
        <span>Ответственный: <b>${esc(appeal.assignee || 'не назначен')}</b></span>
      </div>
    </div>

    <div class="detail-grid">
      <div class="grid" style="gap:16px">
        <div class="card card-pad">
          <div class="section-title">Описание</div>
          <div class="prose">${esc(appeal.description)}</div>
          ${appeal.expected_solution ? `
            <div class="divider"></div>
            <div class="section-title">Предлагаемое решение</div>
            <div class="prose">${esc(appeal.expected_solution)}</div>` : ''}
          ${appeal.attachment_name ? `
            <div class="divider"></div>
            <div class="section-title">Вложение</div>
            <a class="btn btn-ghost btn-sm" href="/api/appeals/${appeal.id}/attachment">${esc(appeal.attachment_name)}</a>` : ''}
        </div>

        ${appeal.resolution ? `
          <div class="card card-pad subcard resolution-box">
            <h4>Результат рассмотрения</h4>
            <div class="prose">${esc(appeal.resolution)}</div>
          </div>` : ''}

        <div class="card">
          <div class="card-head"><h3>История и обсуждение</h3><span class="muted small">${items.length} ${plural(items.length, 'запись', 'записи', 'записей')}</span></div>
          <div class="card-body">
            <div class="timeline">${items.map(timelineItem).join('') || '<div class="muted">Пока нет событий</div>'}</div>
            ${commentForm}
          </div>
        </div>
      </div>

      <aside class="sticky">
        <div class="card card-pad">
          <div class="section-title">Карточка обращения</div>
          <dl class="kv">
            <dt>Категория</dt><dd>${esc(appeal.category || '—')}</dd>
            <dt>Автор</dt><dd>${esc(appeal.author || '—')}</dd>
            <dt>Подразделение</dt><dd>${esc(appeal.author_department || '—')}</dd>
            <dt>Приоритет</dt><dd>${esc(appeal.priority)}</dd>
            <dt>Первый ответ</dt><dd>${appeal.first_response_at ? fmtDateTime(appeal.first_response_at) : 'ещё не было'}</dd>
            <dt>Обновлено</dt><dd>${fmtDateTime(appeal.updated_at)}</dd>
            ${appeal.closed_at ? `<dt>Закрыто</dt><dd>${fmtDateTime(appeal.closed_at)}</dd>` : ''}
          </dl>
        </div>
        ${actionPanel}
      </aside>
    </div>`;

  if ($('#take-btn')) {
    $('#take-btn').addEventListener('click', async () => {
      try {
        await api(`/api/appeals/${appeal.id}/take`, { method: 'POST' });
        toast('Обращение взято в работу', 'success');
        renderDetail(appeal.id);
      } catch (error) { toast(error.message, 'error'); }
    });
  }

  if ($('#status-form')) {
    $('#status-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const status = $('#a-status').value;
      const resolution = $('#a-resolution').value.trim();
      if (!status) { toast('Выберите новый статус', 'error'); return; }
      if (FINAL.includes(status) && resolution.length < 10) {
        toast('Для завершения обращения нужен результат (минимум 10 символов)', 'error');
        return;
      }
      try {
        await api(`/api/appeals/${appeal.id}/status`, {
          method: 'POST',
          body: {
            status,
            resolution,
            comment: $('#a-comment').value.trim(),
            internal: $('#a-internal').checked,
          },
        });
        toast('Статус обновлён', 'success');
        renderDetail(appeal.id);
      } catch (error) { toast(error.message, 'error'); }
    });
  }

  if ($('#assign-btn')) {
    $('#assign-btn').addEventListener('click', async () => {
      const assigneeId = Number($('#a-assignee').value);
      if (!assigneeId) { toast('Выберите сотрудника', 'error'); return; }
      try {
        await api(`/api/appeals/${appeal.id}/assign`, { method: 'POST', body: { assignee_id: assigneeId } });
        toast('Ответственный назначен', 'success');
        renderDetail(appeal.id);
      } catch (error) { toast(error.message, 'error'); }
    });
  }

  if ($('#comment-form')) {
    $('#comment-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const text = $('#c-text').value.trim();
      if (text.length < 2) { toast('Введите комментарий', 'error'); return; }
      try {
        await api(`/api/appeals/${appeal.id}/comments`, {
          method: 'POST',
          body: { text, internal: $('#c-internal') ? $('#c-internal').checked : false },
        });
        toast('Комментарий добавлен', 'success');
        renderDetail(appeal.id);
      } catch (error) { toast(error.message, 'error'); }
    });
  }
}

/* ---------- Аналитика ---------- */

function barRows(data, total) {
  const max = Math.max(1, ...Object.values(data));
  return Object.entries(data).map(([label, value]) => `
    <div class="bar-row">
      <span>${esc(label)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.round((value / max) * 100)}%"></div></div>
      <span class="bar-value">${value}</span>
    </div>`).join('');
}

async function renderDashboard() {
  $('#view').innerHTML = '<div class="empty">Считаем аналитику…</div>';
  let d;
  try {
    d = await api('/api/dashboard');
  } catch (error) {
    $('#view').innerHTML = `<div class="empty"><strong>Аналитика недоступна</strong>${esc(error.message)}</div>`;
    return;
  }
  const k = d.kpi;
  const maxWeek = Math.max(1, ...d.weeks.map((w) => w.count));

  $('#view').innerHTML = `
    <div class="page-head">
      <div>
        <h2>Аналитика обращений</h2>
        <div class="sub">Сводка по всем обращениям: прозрачность рассмотрения, сроки и повторяющиеся проблемы.</div>
      </div>
      <a class="btn btn-ghost btn-sm" href="/api/export.csv">Экспорт в CSV</a>
    </div>

    <div class="grid grid-kpi" style="margin-bottom:16px">
      <div class="kpi accent">
        <span class="label">Всего обращений</span>
        <span class="value">${k.total}</span>
        <span class="delta">${k.open} в работе</span>
      </div>
      <div class="kpi">
        <span class="label">Рассмотрено</span>
        <span class="value">${k.considered_share}<small>%</small></span>
        <span class="delta">${k.considered} из ${k.total}</span>
      </div>
      <div class="kpi ${k.overdue ? 'warn' : ''}">
        <span class="label">Просрочено</span>
        <span class="value">${k.overdue}</span>
        <span class="delta">срок реакции истёк</span>
      </div>
      <div class="kpi">
        <span class="label">Первый ответ</span>
        <span class="value">${k.avg_first_response_hours ?? '—'}<small> ч</small></span>
        <span class="delta">в среднем</span>
      </div>
      <div class="kpi">
        <span class="label">Закрытие</span>
        <span class="value">${k.avg_close_days ?? '—'}<small> дн</small></span>
        <span class="delta">в среднем до результата</span>
      </div>
      <div class="kpi">
        <span class="label">Реализовано</span>
        <span class="value">${k.implemented}</span>
        <span class="delta">${k.rejected} отклонено</span>
      </div>
    </div>

    <div class="grid grid-2" style="margin-bottom:16px">
      <div class="card">
        <div class="card-head"><h3>Статусы</h3><span class="muted small">все обращения</span></div>
        <div class="card-body bar-list">${barRows(d.by_status, k.total)}</div>
      </div>
      <div class="card">
        <div class="card-head"><h3>Типы обращений</h3><span class="muted small">проблемы и предложения</span></div>
        <div class="card-body bar-list">${barRows(d.by_type, k.total)}</div>
      </div>
    </div>

    <div class="grid grid-2" style="margin-bottom:16px">
      <div class="card">
        <div class="card-head"><h3>Категории</h3><span class="muted small">топ по количеству</span></div>
        <div class="card-body">
          <table class="simple">
            <thead><tr><th>Категория</th><th>Всего</th><th>В работе</th><th>Рассмотрено</th></tr></thead>
            <tbody>
              ${d.categories.map((c) => `
                <tr><td>${esc(c.name)}</td><td>${c.total}</td><td>${c.open}</td><td>${c.resolved}</td></tr>`).join('')}
            </tbody>
          </table>
        </div>
      </div>
      <div class="card">
        <div class="card-head"><h3>Динамика по неделям</h3><span class="muted small">создано обращений</span></div>
        <div class="card-body">
          <div class="week-chart">
            ${d.weeks.map((w) => `
              <div class="week-col">
                <span class="n">${w.count}</span>
                <div class="week-bar" style="height:${Math.max(4, Math.round((w.count / maxWeek) * 100))}%"></div>
                <span class="l">${w.label}</span>
              </div>`).join('')}
          </div>
        </div>
      </div>
    </div>

    <div class="grid grid-2" style="margin-bottom:16px">
      <div class="card">
        <div class="card-head"><h3>Повторяющиеся проблемы</h3><span class="muted small">2+ обращения в категории</span></div>
        <div class="card-body">
          ${d.recurring.length ? `<div class="recurring">${d.recurring.map((r) => `
            <div class="recurring-item"><span>${esc(r.category)}</span>
              <span class="count">${r.problems} ${plural(r.problems, 'проблема', 'проблемы', 'проблем')} · ${r.open} открыто</span>
            </div>`).join('')}</div>`
            : '<div class="muted">Пока недостаточно данных для выявления повторов.</div>'}
        </div>
      </div>
      <div class="card">
        <div class="card-head"><h3>Реализованные улучшения</h3><span class="muted small">что уже сделано</span></div>
        <div class="card-body">
          ${d.implemented.length ? `<div class="impl">${d.implemented.map((r) => `
            <div class="impl-item">
              <div class="t"><a href="#/appeal/${r.id}">${esc(r.code)} · ${esc(r.title)}</a></div>
              <div class="d">${esc(r.resolution || '')}</div>
            </div>`).join('')}</div>`
            : '<div class="muted">Пока нет завершённых улучшений.</div>'}
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h3>Просроченные обращения</h3><span class="muted small">${d.overdue.length} в списке</span></div>
      <div class="card-body">
        ${d.overdue.length ? `
          <table class="simple">
            <thead><tr><th>Код</th><th>Обращение</th><th>Ответственный</th><th>Срок</th><th>Приоритет</th></tr></thead>
            <tbody>
              ${d.overdue.map((r) => `
                <tr class="clickable" data-id="${r.id}">
                  <td>${esc(r.code)}</td>
                  <td>${esc(r.title)}</td>
                  <td>${esc(r.assignee || '—')}</td>
                  <td>${fmtDate(r.due_at)}</td>
                  <td>${priorityBadge(r.priority)}</td>
                </tr>`).join('')}
            </tbody>
          </table>` : '<div class="muted">Просроченных обращений нет — отличный результат.</div>'}
      </div>
    </div>`;

  $$('#view tr.clickable').forEach((row) => {
    row.addEventListener('click', () => { location.hash = '#/appeal/' + row.dataset.id; });
  });
}

/* ---------- Администрирование ---------- */

async function renderAdmin() {
  $('#view').innerHTML = '<div class="empty">Загружаем настройки…</div>';
  let data;
  try {
    data = await api('/api/admin/categories');
  } catch (error) {
    $('#view').innerHTML = `<div class="empty"><strong>Недоступно</strong>${esc(error.message)}</div>`;
    return;
  }
  const users = state.meta.users.filter((u) => u.role !== 'employee');
  const userOptions = (current) => optionList(
    users.map((u) => ({ value: u.id, label: `${u.name} — ${u.position || u.department}` })),
    current,
    'Без ответственного',
  );

  $('#view').innerHTML = `
    <div class="page-head">
      <div>
        <h2>Настройки маршрутизации</h2>
        <div class="sub">Категории определяют, кому автоматически направляется обращение.</div>
      </div>
    </div>

    <div class="admin-grid">
      <div class="card">
        <div class="card-head"><h3>Категории и ответственные</h3><span class="muted small">${data.categories.length}</span></div>
        <div class="card-body">
          <div class="cat-row cat-head">
            <span>Категория</span><span>Ответственный</span><span>Активна</span><span></span>
          </div>
          ${data.categories.map((c) => `
            <div class="cat-row" data-cat="${c.id}">
              <input type="text" class="cat-name" value="${esc(c.name)}" maxlength="80">
              <select class="cat-resp">${userOptions(c.responsible_id)}</select>
              <label class="check"><input type="checkbox" class="cat-active" ${c.active ? 'checked' : ''}> вкл.</label>
              <button class="btn btn-ghost btn-sm cat-save" type="button">Сохранить</button>
            </div>`).join('')}
        </div>
      </div>

      <div class="grid" style="gap:16px">
        <form class="card card-pad" id="cat-form">
          <div class="section-title">Новая категория</div>
          <label class="field">
            <span>Название</span>
            <input id="nc-name" type="text" maxlength="80" placeholder="Например: Работа с клиентами">
          </label>
          <label class="field">
            <span>Описание</span>
            <textarea id="nc-desc" maxlength="300" style="min-height:64px" placeholder="Что относится к категории"></textarea>
          </label>
          <label class="field">
            <span>Ответственный</span>
            <select id="nc-resp">${userOptions('')}</select>
          </label>
          <button class="btn btn-primary btn-block" type="submit">Создать категорию</button>
        </form>

        <div class="card card-pad">
          <div class="section-title">Как работает маршрутизация</div>
          <div class="routing-note">
            При создании обращения система определяет категорию и назначает ответственного из настроек категории.
            Если ответственный не задан или деактивирован, обращение автоматически направляется администратору —
            обращение не теряется. Руководитель может переназначить ответственного в карточке обращения.
          </div>
        </div>
      </div>
    </div>`;

  $$('.cat-save').forEach((button) => {
    button.addEventListener('click', async () => {
      const row = button.closest('.cat-row');
      const id = Number(row.dataset.cat);
      try {
        await api('/api/admin/categories/' + id, {
          method: 'PATCH',
          body: {
            name: $('.cat-name', row).value.trim(),
            responsible_id: Number($('.cat-resp', row).value) || null,
            active: $('.cat-active', row).checked,
          },
        });
        toast('Категория сохранена', 'success');
        renderAdmin();
      } catch (error) { toast(error.message, 'error'); }
    });
  });

  $('#cat-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await api('/api/admin/categories', {
        method: 'POST',
        body: {
          name: $('#nc-name').value.trim(),
          description: $('#nc-desc').value.trim(),
          responsible_id: Number($('#nc-resp').value) || null,
        },
      });
      toast('Категория создана', 'success');
      renderAdmin();
    } catch (error) { toast(error.message, 'error'); }
  });
}

/* ---------- Старт ---------- */

function bindStaticHandlers() {
  $('#login-form').addEventListener('submit', (event) => {
    event.preventDefault();
    doLogin($('#login-login').value.trim(), $('#login-password').value);
  });

  $$('.demo-btn').forEach((button) => {
    button.addEventListener('click', () => {
      $('#login-login').value = button.dataset.login;
      $('#login-password').value = 'demo123';
      $('#login-form').requestSubmit();
    });
  });

  $('#logout').addEventListener('click', async () => {
    try { await api('/api/logout', { method: 'POST' }); } catch (error) { /* ignore */ }
    state.user = null;
    state.meta = null;
    state.appeals = [];
    showLogin();
    location.hash = '';
  });

  window.addEventListener('hashchange', route);
}

async function boot() {
  bindStaticHandlers();
  try {
    const me = await api('/api/me');
    state.user = me.user;
    await loadMeta();
    showApp();
    if (!location.hash) location.hash = '#/appeals';
    route();
  } catch (error) {
    showLogin();
  }
}

document.addEventListener('DOMContentLoaded', boot);
