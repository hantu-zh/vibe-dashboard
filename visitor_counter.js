// visitor-counter.js - 访问统计
(function() {
  const VISITOR_DATA_URL = 'visitor_data.json';

  // 获取今日日期
  function getToday() {
    return new Date().toISOString().split('T')[0];
  }

  // 生成简单访客ID（基于浏览器指纹）
  function getVisitorId() {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    ctx.textBaseline = 'top';
    ctx.font = '14px Arial';
    ctx.fillText('visitor', 2, 2);
    const fingerprint = canvas.toDataURL().slice(-50);
    return fingerprint.replace(/[^a-zA-Z0-9]/g, '').substring(0, 16);
  }

  // 更新访问计数
  async function updateVisitorCount() {
    const visitorId = getVisitorId();
    const today = getToday();

    try {
      // 读取现有数据
      const response = await fetch(VISITOR_DATA_URL + '?t=' + Date.now());
      const data = response.ok ? await response.json() : { dates: {}, visitors: {} };

      // 更新今日计数
      if (!data.dates[today]) {
        data.dates[today] = { pv: 0, uv: new Set() };
      }
      data.dates[today].pv += 1;

      // 检查是否是新访客
      if (!data.visitors[visitorId] || data.visitors[visitorId] !== today) {
        data.visitors[visitorId] = today;
        // UV 需要去重，这里简化处理
      }

      // 更新显示
      const totalPv = Object.values(data.dates).reduce((sum, d) => sum + d.pv, 0);
      const todayPv = data.dates[today].pv;

      // 显示统计
      const statsEl = document.getElementById('visitor-stats');
      if (statsEl) {
        statsEl.innerHTML = `今日PV: ${todayPv} | 总PV: ${totalPv}`;
      }

    } catch (e) {
      console.log('[Visitor Counter] Error:', e.message);
      // 显示基础统计
      const statsEl = document.getElementById('visitor-stats');
      if (statsEl) {
        statsEl.innerHTML = '访问统计加载中...';
      }
    }
  }

  // 页面加载后执行
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', updateVisitorCount);
  } else {
    updateVisitorCount();
  }
})();
