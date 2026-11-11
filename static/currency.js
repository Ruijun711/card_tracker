(() => {
  'use strict';
  const input = document.getElementById('currency-divisor');
  if (!input) return;
  const message = document.getElementById('currency-message');
  const cells = document.querySelectorAll('[data-jpy]');
  const key = 'ua-card-tracker-jpy-divisor-v1';
  const formatter = new Intl.NumberFormat('zh-TW', {
    minimumFractionDigits: 0, maximumFractionDigits: 0
  });
  function update() {
    const text = input.value.trim();
    const divisor = Number(text);
    const badInput = Boolean(input.validity && input.validity.badInput);
    const valid = !badInput && text !== '' && Number.isFinite(divisor) && divisor > 0;
    const invalid = badInput || (text !== '' && !valid);
    input.setAttribute('aria-invalid', String(invalid));
    message.classList.toggle('is-error', invalid);
    message.textContent = valid
      ? `目前換算：日幣價格 ÷ ${text}，台幣參考價無條件捨去小數，只顯示整數。`
      : invalid ? '請輸入大於 0 的有效數字，不能使用 0 或負數。'
      : '輸入 x 後顯示台幣參考價。';
    cells.forEach(cell => {
      const amount = Number(cell.dataset.jpy) / divisor;
      cell.textContent = !valid ? '—'
        : Number.isFinite(amount) ? `NT$ ${formatter.format(Math.floor(amount))}`
        : '超出可顯示範圍';
    });
    try {
      if (valid) localStorage.setItem(key, text);
      else localStorage.removeItem(key);
    } catch (_) { /* Calculation still works when browser storage is unavailable. */ }
  }
  try { input.value = localStorage.getItem(key) || ''; } catch (_) {}
  input.addEventListener('input', update);
  document.getElementById('currency-clear').addEventListener('click', () => {
    input.value = ''; update(); input.focus();
  });
  window.addEventListener('pageshow', update);
  update();
})();
