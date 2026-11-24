'use strict';
const loginConfig = Object.assign({apiBase: '', successUrl: '/'}, window.QIMING_LOGIN_CONFIG || {});
const apiUrl = path => `${String(loginConfig.apiBase || '').replace(/\/$/, '')}${path}`;
const $ = id => document.getElementById(id);
const captchaIds = {login: '', register: ''};
const captchaRequests = {login: null, register: null};
let redirectTimer;

function showError(target, message) {
  const box = $(target);
  box.textContent = message;
  box.hidden = !message;
}

function fieldError(name, message = '') {
  const field = $(name + 'Field');
  const error = $(name + 'Error');
  field.classList.toggle('invalid', Boolean(message));
  error.textContent = message;
}

function refreshCaptcha(mode = 'login') {
  if (captchaRequests[mode]) return captchaRequests[mode];
  const image = $(mode === 'login' ? 'captchaImage' : 'registerCaptchaImage');
  image.classList.add('is-loading');
  image.setAttribute('aria-busy', 'true');
  captchaRequests[mode] = fetch(apiUrl('/api/auth/captcha'), {cache: 'no-store'})
    .then(async response => {
      if (!response.ok) throw new Error('验证码加载失败，请重试');
      const data = await response.json();
      captchaIds[mode] = data.captcha_id;
      image.src = data.image;
    })
    .finally(() => {
      captchaRequests[mode] = null;
      image.classList.remove('is-loading');
      image.removeAttribute('aria-busy');
    });
  return captchaRequests[mode];
}

function setMode(mode, updateHistory = true) {
  const isRegister = mode === 'register';
  const current = isRegister ? $('loginForm') : $('registerForm');
  const next = isRegister ? $('registerForm') : $('loginForm');
  if (current.hidden) return;
  current.classList.add('switch-out');
  setTimeout(() => {
    current.hidden = true;
    current.classList.remove('switch-out');
    next.hidden = false;
    next.classList.add('switch-in');
    requestAnimationFrame(() => next.classList.remove('switch-in'));
  }, 130);
  if (updateHistory) {
    const url = new URL(location.href);
    if (isRegister) url.searchParams.set('mode', 'register');
    else url.searchParams.delete('mode');
    history.pushState({mode}, '', url);
  }
  showError('loginError', '');
  showError('registerError', '');
  refreshCaptcha(mode).catch(error => showError(isRegister ? 'registerError' : 'loginError', error.message));
}

function togglePassword(button, input) {
  input.type = input.type === 'password' ? 'text' : 'password';
  const visible = input.type === 'text';
  button.classList.toggle('is-visible', visible);
  button.setAttribute('aria-pressed', String(visible));
  button.title = visible ? '隐藏密码' : '显示密码';
  button.setAttribute('aria-label', button.title);
}

async function existingSession() {
  const token = sessionStorage.getItem('qh_token') || localStorage.getItem('qh_token');
  if (!token) return false;
  const response = await fetch(apiUrl('/api/auth/session'), {headers: {Authorization: 'Bearer ' + token}});
  if (!response.ok) return false;
  sessionStorage.setItem('qh_token', token);
  document.cookie = `qh_token=${encodeURIComponent(token)}; Path=/; SameSite=Lax; Max-Age=${localStorage.getItem('qh_token') ? 2592000 : 43200}`;
  window.location.replace(loginConfig.successUrl);
  return true;
}

async function login(event) {
  event.preventDefault();
  showError('loginError', '');
  ['username', 'password', 'captcha'].forEach(name => fieldError(name));
  let valid = true;
  if (!$('username').value.trim()) { fieldError('username', '请输入账号'); valid = false; }
  if (!$('password').value) { fieldError('password', '请输入密码'); valid = false; }
  if (!$('captcha').value.trim()) { fieldError('captcha', '请输入验证码'); valid = false; }
  if (!valid) return;
  const button = $('submitButton');
  button.disabled = true;
  button.innerHTML = '<span class="spinner" aria-hidden="true"></span>登录中';
  try {
    const response = await fetch(apiUrl('/api/auth/login'), {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
      username: $('username').value.trim(), password: $('password').value, captcha_id: captchaIds.login, captcha: $('captcha').value.trim(), remember: $('remember').checked
    })});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '登录失败，请检查账号和密码');
    sessionStorage.setItem('qh_token', data.token);
    if ($('remember').checked) localStorage.setItem('qh_token', data.token); else localStorage.removeItem('qh_token');
    document.cookie = `qh_token=${encodeURIComponent(data.token)}; Path=/; SameSite=Lax; Max-Age=${$('remember').checked ? 2592000 : 43200}`;
    localStorage.setItem('qh_username', $('username').value.trim());
    window.location.replace(loginConfig.successUrl);
  } catch (error) {
    showError('loginError', error.message || '登录失败，请稍后重试');
    $('captcha').value = '';
    await refreshCaptcha('login').catch(() => {});
  } finally {
    button.disabled = false;
    button.textContent = '登 录';
  }
}

const registrationValidators = {
  registerUsername: value => /^[A-Za-z0-9]{4,20}$/.test(value) ? '' : '请输入4-20位字母或数字',
  registerEmail: value => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) ? '' : '请输入有效邮箱地址',
  registerPassword: value => /^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d]{6,20}$/.test(value) ? '' : '密码需为6-20位，且包含字母和数字',
  registerConfirm: value => value && value === $('registerPassword').value ? '' : '两次输入的密码不一致',
  registerCaptcha: value => value.trim() ? '' : '请输入验证码'
};

function validateRegistration(id) {
  const message = registrationValidators[id]($(id).value);
  fieldError(id.replace(/^register/, 'register'), message);
  return !message;
}

async function register(event) {
  event.preventDefault();
  showError('registerError', '');
  const ids = Object.keys(registrationValidators);
  const valid = ids.map(validateRegistration).every(Boolean);
  if (!valid) return;
  if (!$('registerTerms').checked) { showError('registerError', '请先阅读并同意用户服务协议和隐私政策'); return; }
  const button = $('registerSubmit');
  button.disabled = true;
  button.innerHTML = '<span class="spinner" aria-hidden="true"></span>注册中';
  try {
    const response = await fetch(apiUrl('/api/auth/register'), {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
      username: $('registerUsername').value.trim(), email: $('registerEmail').value.trim(),
      password: $('registerPassword').value, confirm_password: $('registerConfirm').value,
      captcha_id: captchaIds.register, captcha: $('registerCaptcha').value.trim(), terms: $('registerTerms').checked
    })});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || (response.status === 409 ? '该账号已存在' : '注册失败，请检查填写内容'));
    $('username').value = data.username;
    $('registerSuccess').showModal();
    let seconds = 3;
    $('redirectCountdown').textContent = seconds;
    redirectTimer = setInterval(() => {
      seconds -= 1;
      $('redirectCountdown').textContent = seconds;
      if (seconds <= 0) {
        clearInterval(redirectTimer);
        $('registerSuccess').close();
        $('password').value = '';
        $('captcha').value = '';
        setMode('login');
      }
    }, 1000);
  } catch (error) {
    showError('registerError', error.message || '注册失败，请稍后重试');
    $('registerCaptcha').value = '';
    await refreshCaptcha('register').catch(() => {});
  } finally {
    button.disabled = !$('registerTerms').checked;
    button.textContent = '注 册';
  }
}

$('loginForm').addEventListener('submit', login);
$('registerForm').addEventListener('submit', register);
$('togglePassword').addEventListener('click', () => togglePassword($('togglePassword'), $('password')));
document.querySelectorAll('[data-toggle-password]').forEach(button => button.addEventListener('click', () => {
  togglePassword(button, $(button.dataset.togglePassword));
}));
document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => setMode(button.dataset.mode)));
document.querySelectorAll('[data-policy]').forEach(button => button.addEventListener('click', () => {
  const privacy = button.dataset.policy === 'privacy';
  $('policyTitle').textContent = privacy ? '隐私政策' : '用户服务协议';
  $('policyContent').textContent = privacy ? '平台仅将账号与邮箱用于身份验证、账号安全和服务通知。' : '注册账号仅供访问启明智能陪护管理平台，请妥善保管登录凭据并遵守平台管理规范。';
  $('policyDialog').showModal();
}));
document.querySelector('[data-close-policy]').addEventListener('click', () => $('policyDialog').close());
$('captchaImage').addEventListener('click', () => refreshCaptcha('login').catch(error => showError('loginError', error.message)));
$('captchaImage').addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') refreshCaptcha('login').catch(error => showError('loginError', error.message)); });
$('registerCaptchaImage').addEventListener('click', () => refreshCaptcha('register').catch(error => showError('registerError', error.message)));
$('registerCaptchaImage').addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') refreshCaptcha('register').catch(error => showError('registerError', error.message)); });
$('forgot').addEventListener('click', () => showError('loginError', '请联系系统管理员重置账号密码'));
$('registerTerms').addEventListener('change', () => { $('registerSubmit').disabled = !$('registerTerms').checked; });
for (const id of Object.keys(registrationValidators)) $(id).addEventListener('blur', () => validateRegistration(id));
for (const id of ['registerUsername', 'registerEmail', 'registerPassword', 'registerConfirm']) $(id).addEventListener('input', () => {
  if ($(id + 'Field').classList.contains('invalid')) validateRegistration(id);
  if (id === 'registerPassword' && $('registerConfirm').value) validateRegistration('registerConfirm');
});
window.addEventListener('popstate', () => setMode(new URLSearchParams(location.search).get('mode') === 'register' ? 'register' : 'login', false));
const remembered = localStorage.getItem('qh_username') || new URLSearchParams(location.search).get('username');
if (remembered) $('username').value = remembered;
const initialMode = new URLSearchParams(location.search).get('mode') === 'register' ? 'register' : 'login';
if (initialMode === 'register') {
  // Select the initial form before the first paint; the shared brand area stays in place.
  $('loginForm').hidden = true;
  $('registerForm').hidden = false;
}
existingSession().catch(() => {}).finally(() => refreshCaptcha(initialMode).catch(error => showError(initialMode === 'register' ? 'registerError' : 'loginError', error.message)));
