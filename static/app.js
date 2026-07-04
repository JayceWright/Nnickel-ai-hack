// ─── Config ───────────────────────────────────────────────────────────────────
const API = '';  // пустой = тот же сервер

// Цвета по типу узла (Glassmorphism & Neon)
const NODE_COLORS = {
  Material:     { background: 'rgba(59, 130, 246, 0.2)', border: '#00e5ff', highlight: { background: 'rgba(59, 130, 246, 0.4)', border: '#00ffff' } },
  Process:      { background: 'rgba(16, 185, 129, 0.2)', border: '#00ff9d', highlight: { background: 'rgba(16, 185, 129, 0.4)', border: '#6ee7b7' } },
  Experiment:   { background: 'rgba(245, 158, 11, 0.2)', border: '#f59e0b', highlight: { background: 'rgba(245, 158, 11, 0.4)', border: '#fbbf24' } },
  Equipment:    { background: 'rgba(139, 92, 246, 0.2)', border: '#a78bfa', highlight: { background: 'rgba(139, 92, 246, 0.4)', border: '#c4b5fd' } },
  Property:     { background: 'rgba(239, 68, 68, 0.2)',  border: '#ff2a5f', highlight: { background: 'rgba(239, 68, 68, 0.4)',  border: '#ff8a9f' } },
  Expert:       { background: 'rgba(249, 115, 22, 0.2)', border: '#fb923c', highlight: { background: 'rgba(249, 115, 22, 0.4)', border: '#fdba74' } },
  Publication:  { background: 'rgba(148, 163, 184, 0.2)',border: '#94a3b8', highlight: { background: 'rgba(148, 163, 184, 0.4)',border: '#cbd5e1' } },
  Organization: { background: 'rgba(56, 189, 248, 0.2)', border: '#7dd3fc', highlight: { background: 'rgba(56, 189, 248, 0.4)', border: '#bae6fd' } },
  Condition:    { background: 'rgba(129, 140, 248, 0.2)',border: '#818cf8', highlight: { background: 'rgba(129, 140, 248, 0.4)',border: '#a5b4fc' } },
};

const DEFAULT_COLOR = { background: 'rgba(255, 255, 255, 0.1)', border: '#00e5ff', highlight: { background: 'rgba(255, 255, 255, 0.2)', border: '#00ffff' } };

// ─── State ────────────────────────────────────────────────────────────────────
let network = null;
let allNodes = [];
let allEdges = [];
let physicsEnabled = true;
let chatHistory = JSON.parse(localStorage.getItem('norngraph_chat')) || [];

// ─── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  restoreChat();
  await checkHealth();
  await loadStats();
  await loadGraph();
  switchPanel('graph');
});

// ─── Health Check ─────────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const res = await fetch(`${API}/api/health`);
    const dot = document.getElementById('status-dot');
    if (res.ok) {
      dot.classList.add('connected');
      dot.title = 'Подключено к Neo4j';
    } else {
      dot.classList.add('error');
      dot.title = 'Ошибка подключения';
    }
  } catch {
    document.getElementById('status-dot').classList.add('error');
  }
}

// ─── Stats ────────────────────────────────────────────────────────────────────
async function loadStats() {
  try {
    const data = await fetch(`${API}/api/stats`).then(r => r.json());
    document.getElementById('stat-nodes').textContent = `${data.total_nodes} узлов`;
    document.getElementById('stat-edges').textContent = `${data.total_edges} связей`;
    document.getElementById('stat-contradictions').textContent = `${data.contradictions} противоречий`;
  } catch (e) {
    console.warn('Stats error:', e);
  }
}

// ─── Graph ────────────────────────────────────────────────────────────────────
async function loadGraph() {
  try {
    const data = await fetch(`${API}/api/graph?limit=300`).then(r => r.json());

    allNodes = data.nodes.map(n => ({
      id: n.id,
      label: truncate(n.label, 22),
      title: n.title || n.label,
      group: n.group,
      color: NODE_COLORS[n.group] || DEFAULT_COLOR,
      font: { color: '#e2e8f0', size: 11, face: 'Inter' },
      size: getNodeSize(n.group),
      borderWidth: 1.5,
      shape: getNodeShape(n.group),
    }));

    allEdges = data.edges.map((e, i) => {
      const isContradict = e.label === 'contradicts';
      return {
        id: i,
        from: e.from,
        to: e.to,
        label: e.label,
        title: e.title || e.label,
        color: isContradict ? { color: '#ff2a5f', highlight: '#ff8a9f' } : { color: 'rgba(255, 255, 255, 0.15)', highlight: '#00e5ff' },
        dashes: isContradict ? [5, 5] : false,
        arrows: 'to',
        font: { color: isContradict ? '#ff8a9f' : '#94a3b8', size: 9, align: 'middle', strokeWidth: 0 },
        width: isContradict ? 2 : 1,
        shadow: isContradict ? { enabled: true, color: 'rgba(255, 42, 95, 0.5)', size: 10, x: 0, y: 0 } : false
      };
    });

    renderGraph(allNodes, allEdges);
  } catch (e) {
    console.error('Graph load error:', e);
  }
}

function getNodeSize(group) {
  const sizes = { Experiment: 20, Material: 16, Process: 15, Expert: 13, Publication: 11 };
  return sizes[group] || 12;
}

function getNodeShape(group) {
  const shapes = {
    Experiment: 'dot', Material: 'diamond', Process: 'box',
    Expert: 'ellipse', Publication: 'triangleDown', Equipment: 'star',
    Property: 'dot', Organization: 'ellipse', Condition: 'dot'
  };
  return shapes[group] || 'dot';
}

function truncate(str, max) {
  if (!str) return '?';
  return str.length > max ? str.slice(0, max) + '…' : str;
}

// ─── Search Nodes ─────────────────────────────────────────────────────────────
function searchNodes() {
  const query = document.getElementById('node-search').value.toLowerCase().trim();
  if (!query || !network) {
    if (network) network.unselectAll();
    return;
  }
  
  const foundIds = allNodes
    .filter(n => n.label.toLowerCase().includes(query) || (n.title && n.title.toLowerCase().includes(query)))
    .map(n => n.id);
    
  if (foundIds.length > 0) {
    network.selectNodes(foundIds);
  } else {
    network.unselectAll();
  }
}

function focusSearchedNodes() {
  const query = document.getElementById('node-search').value.toLowerCase().trim();
  if (!query || !network) return;
  
  const foundIds = allNodes
    .filter(n => n.label.toLowerCase().includes(query) || (n.title && n.title.toLowerCase().includes(query)))
    .map(n => n.id);
    
  if (foundIds.length > 0) {
    switchPanel('graph');
    network.fit({ nodes: foundIds, animation: { duration: 500, easingFunction: 'easeInOutQuad' } });
  }
}

function renderGraph(nodes, edges) {
  const container = document.getElementById('graph-container');
  const dataset = {
    nodes: new vis.DataSet(nodes),
    edges: new vis.DataSet(edges)
  };

  const options = {
    physics: {
      enabled: physicsEnabled,
      solver: 'barnesHut',
      barnesHut: { gravitationalConstant: -2000, centralGravity: 0.3, springLength: 95, springConstant: 0.04, damping: 0.09 },
      stabilization: false
    },
    interaction: {
      hover: true,
      tooltipDelay: 200,
      navigationButtons: false,
      zoomView: true,
    },
    edges: {
      smooth: { type: 'continuous', roundness: 0.2 },
    },
    nodes: {
      scaling: { min: 8, max: 25 }
    },
    groups: NODE_COLORS
  };

  if (network) network.destroy();
  network = new vis.Network(container, dataset, options);

  // Клик по узлу → показываем детали
  network.on('click', params => {
    if (params.nodes.length > 0) {
      const nodeId = params.nodes[0];
      const node = nodes.find(n => n.id === nodeId);
      if (node) showNodeDetail(node);
    } else {
      hideNodeDetail();
    }
  });

  // Подсветка новых узлов (если есть)
  network.on('stabilizationIterationsDone', () => {
    network.setOptions({ physics: { enabled: false } });
    physicsEnabled = false;
    network.fit();
  });

}

function showNodeDetail(node) {
  const panel = document.getElementById('node-detail');
  const content = document.getElementById('node-detail-content');
  panel.style.display = 'block';
  content.innerHTML = `
    <div class="detail-type">${node.group}</div>
    <div class="detail-name">${node.title || node.label}</div>
  `;
}

function hideNodeDetail() {
  document.getElementById('node-detail').style.display = 'none';
}

function fitGraph() {
  if (network) network.fit({ animation: { duration: 500, easingFunction: 'easeInOutQuad' } });
}

function togglePhysics() {
  physicsEnabled = !physicsEnabled;
  if (network) network.setOptions({ physics: { enabled: physicsEnabled } });
}

function filterGraph() {
  const type = document.getElementById('filter-type').value;
  if (!type) {
    renderGraph(allNodes, allEdges);
    return;
  }
  const filtered = allNodes.filter(n => n.group === type);
  const filteredIds = new Set(filtered.map(n => n.id));
  const filteredEdges = allEdges.filter(e => filteredIds.has(e.from) && filteredIds.has(e.to));
  renderGraph(filtered, filteredEdges);
}

// ─── Panel Switching ──────────────────────────────────────────────────────────
function switchPanel(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));

  document.getElementById(`panel-${name}`).classList.add('active');
  document.querySelector(`[data-panel="${name}"]`).classList.add('active');

  if (name === 'graph' && network) {
    // Принудительно заставляем vis-network пересчитать размеры холста
    setTimeout(() => {
      network.setSize('100%', '100%');
      network.redraw();
      network.fit();
    }, 50);
  }

  if (name === 'analytics') loadAnalytics();
}


// ─── Q&A ──────────────────────────────────────────────────────────────────────
function saveChatHistory() {
  localStorage.setItem('norngraph_chat', JSON.stringify(chatHistory));
}

function clearChat() {
  chatHistory = [];
  saveChatHistory();
  const container = document.getElementById('chat-messages');
  container.innerHTML = `
    <div class="chat-welcome">
      <div class="welcome-icon">🔬</div>
      <div class="welcome-text">Задайте вопрос о металлургических процессах. Система проанализирует граф знаний и сформирует ответ.</div>
      <div class="example-questions">
        <button class="example-btn" onclick="fillQuestion('Что делали по флотации медно-никелевых концентратов и какой был эффект?')">
          Флотация медно-никелевых концентратов
        </button>
        <button class="example-btn" onclick="fillQuestion('Какое оборудование используется для выщелачивания никеля?')">
          Оборудование для выщелачивания
        </button>
        <button class="example-btn" onclick="fillQuestion('Какие противоречия есть в данных по извлечению платиноидов?')">
          Противоречия по платиноидам
        </button>
      </div>
    </div>
  `;
}

function restoreChat() {
  if (chatHistory.length > 0) {
    const welcome = document.querySelector('.chat-welcome');
    if (welcome) welcome.remove();
    chatHistory.forEach(msg => {
      addChatMessage(msg.text, msg.role, null, false, false);
    });
  }
}

function fillQuestion(text) {
  document.getElementById('qa-input').value = text;
  sendQuestion();
}

function handleQAKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendQuestion();
  }
}

async function sendQuestion() {
  const input = document.getElementById('qa-input');
  const question = input.value.trim();
  if (!question) return;

  const btn = document.getElementById('send-btn');
  btn.disabled = true;
  input.value = '';

  // Убираем welcome screen
  const welcome = document.querySelector('.chat-welcome');
  if (welcome) welcome.remove();

  // Добавляем сообщение пользователя
  addChatMessage(question, 'user');

  // Добавляем загрузчик
  const loadingId = 'loading-' + Date.now();
  addChatMessage('', 'assistant', loadingId, true);

  try {
    const res = await fetch(`${API}/api/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, max_hops: 2 })
    });
    const data = await res.json();

    // Заменяем загрузчик ответом
    const loading = document.getElementById(loadingId);
    if (loading) {
      const bubble = loading.querySelector('.chat-bubble');
      bubble.classList.remove('loading');
      if (typeof marked !== 'undefined') {
        bubble.innerHTML = marked.parse(data.answer);
      } else {
        bubble.textContent = data.answer;
      }
      chatHistory.push({ text: data.answer, role: 'assistant' });
      saveChatHistory();
      document.getElementById('chat-messages').scrollTop = document.getElementById('chat-messages').scrollHeight;
    }
  } catch (e) {
    const loading = document.getElementById(loadingId);
    if (loading) {
      const bubble = loading.querySelector('.chat-bubble');
      bubble.classList.remove('loading');
      bubble.textContent = 'Ошибка запроса. Проверьте подключение к серверу.';
      bubble.style.borderColor = 'rgba(239,68,68,0.4)';
    }
  }

  btn.disabled = false;
  input.focus();
}

function addChatMessage(text, role, id = null, isLoading = false, save = true) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = `chat-message ${role}`;
  if (id) div.id = id;

  const emoji = role === 'user' ? '👤' : '🤖';
  
  let contentHtml = text || (isLoading ? 'Анализирую граф знаний...' : '');
  if (!isLoading && role === 'assistant' && text && typeof marked !== 'undefined') {
    contentHtml = marked.parse(text);
  } else if (!isLoading && role === 'user') {
    contentHtml = text.replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  div.innerHTML = `
    <div class="chat-avatar">${emoji}</div>
    <div class="chat-bubble ${isLoading ? 'loading' : ''}">${contentHtml}</div>
  `;

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;

  if (save && text && !isLoading) {
    chatHistory.push({ text, role });
    saveChatHistory();
  }
}

// ─── Analytics ────────────────────────────────────────────────────────────────
async function loadAnalytics() {
  try {
    const data = await fetch(`${API}/api/analytics`).then(r => r.json());

    const cList = document.getElementById('contradictions-list');
    cList.innerHTML = '';
    if (data.contradictions.length === 0) {
      cList.innerHTML = '<div class="empty-state">Противоречий не найдено</div>';
    } else {
      data.contradictions.forEach(c => {
        const item = document.createElement('div');
        item.className = 'contradiction-item';
        item.innerHTML = `
          <div class="contradiction-nodes"><strong>${c.from}</strong> ↔ <strong>${c.to}</strong></div>
          <div class="contradiction-reason">⚠ ${c.reason}</div>
        `;
        cList.appendChild(item);
      });
    }

    const gList = document.getElementById('gaps-list');
    gList.innerHTML = '';
    if (data.gaps.length === 0) {
      gList.innerHTML = '<div class="empty-state">Изолированных узлов не найдено</div>';
    } else {
      data.gaps.forEach(g => {
        const item = document.createElement('div');
        item.className = 'gap-item';
        item.textContent = `📦 ${g.name}`;
        gList.appendChild(item);
      });
    }
  } catch (e) {
    console.error('Analytics error:', e);
  }
}

// ─── Upload ───────────────────────────────────────────────────────────────────
function handleDragover(e) {
  e.preventDefault();
  document.getElementById('upload-zone').classList.add('dragging');
}

function handleDragleave() {
  document.getElementById('upload-zone').classList.remove('dragging');
}

function handleDrop(e) {
  e.preventDefault();
  document.getElementById('upload-zone').classList.remove('dragging');
  const files = Array.from(e.dataTransfer.files);
  if (files.length > 0) uploadFiles(files);
}

function handleFileSelect(e) {
  const files = Array.from(e.target.files);
  if (files.length > 0) uploadFiles(files);
}

async function uploadFiles(files) {
  const statusDiv  = document.getElementById('upload-status');
  const statusText = document.getElementById('upload-status-text');
  const progressBar = document.getElementById('progress-bar');
  const resultDiv  = document.getElementById('upload-result');

  statusDiv.style.display = 'block';
  resultDiv.innerHTML = '';
  
  let totalNodesCreated = 0;
  let totalEdgesCreated = 0;
  let allNewNodes = [];

  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    statusText.textContent = `[${i + 1}/${files.length}] 📄 Загрузка файла: ${file.name}...`;
    progressBar.style.width = '10%';
    progressBar.style.background = 'linear-gradient(90deg, var(--accent), var(--green))';

    const formData = new FormData();
    formData.append('file', file);
    const useVision = document.getElementById('use-vision-toggle')?.checked || false;
    formData.append('use_vision', useVision);

    try {
      const res = await fetch(`${API}/api/upload`, { method: 'POST', body: formData });
      if (!res.ok) throw new Error((await res.json()).detail);

      const { job_id } = await res.json();
      progressBar.style.width = '30%';
      statusText.textContent = `[${i + 1}/${files.length}] 🔍 Извлечение сущностей для ${file.name}...`;

      const status = await pollUploadStatus(job_id, progressBar, statusText, resultDiv);
      if (status) {
        totalNodesCreated += status.nodes_created;
        totalEdgesCreated += status.edges_created;
        allNewNodes = allNewNodes.concat(status.new_nodes || []);
      }
    } catch (e) {
      statusText.textContent = `❌ Ошибка при обработке ${file.name}: ${e.message}`;
      progressBar.style.background = '#ef4444';
      await new Promise(r => setTimeout(r, 3000));
    }
  }

  statusText.textContent = `✅ Успешно обработано файлов: ${files.length}`;
  progressBar.style.width = '100%';
  
  await loadGraph();
  await loadStats();

  resultDiv.innerHTML = `
    <strong>Всего добавлено в граф:</strong><br>
    🔵 Новых узлов: <strong>${totalNodesCreated}</strong><br>
    🔗 Новых связей: <strong>${totalEdgesCreated}</strong><br><br>
    <strong>Всего созданных узлов в этой сессии:</strong><br>
    ${allNewNodes.map(n =>
      `<span class="result-node" style="background: rgba(59,130,246,0.15); border: 1px solid rgba(59,130,246,0.4); color: #60a5fa;">${n.label}: ${n.name}</span>`
    ).join('')}
  `;

  if (allNewNodes.length > 0) {
    const newIds = allNewNodes.map(n => n.id);
    switchPanel('graph');
    setTimeout(() => {
      network.selectNodes(newIds);
      network.fit({
        nodes: newIds,
        animation: { duration: 1000, easingFunction: 'easeInOutQuad' }
      });
    }, 500);
  }
}

async function pollUploadStatus(jobId, progressBar, statusText, resultDiv) {
  for (let i = 0; i < 30; i++) {
    await new Promise(r => setTimeout(r, 2000));

    const status = await fetch(`${API}/api/upload/status/${jobId}`).then(r => r.json());

    if (status.status === 'done') {
      progressBar.style.width = '100%';
      statusText.textContent = `✅ Обработка завершена!`;
      resultDiv.innerHTML = `
        <strong>Добавлено в граф:</strong><br>
        🔵 Новых узлов: <strong>${status.nodes_created}</strong><br>
        🔗 Новых связей: <strong>${status.edges_created}</strong><br><br>
        <strong>Созданные узлы:</strong><br>
        ${status.new_nodes.map(n =>
          `<span class="result-node" style="background: rgba(59,130,246,0.15); border: 1px solid rgba(59,130,246,0.4); color: #60a5fa;">${n.label}: ${n.name}</span>`
        ).join('')}
      `;
      return status;
    }

    if (status.status === 'error') {
      throw new Error(status.error || 'Неизвестная ошибка');
    }

    progressBar.style.width = Math.min(50 + i * 2, 90) + '%';
  }
  throw new Error('Таймаут обработки');
}

async function uploadByUrl() {
  const urlInput = document.getElementById('url-input');
  const url = urlInput.value.trim();
  if (!url) {
    alert('Пожалуйста, введите корректную ссылку.');
    return;
  }

  const statusDiv  = document.getElementById('upload-status');
  const statusText = document.getElementById('upload-status-text');
  const progressBar = document.getElementById('progress-bar');
  const resultDiv  = document.getElementById('upload-result');

  statusDiv.style.display = 'block';
  resultDiv.innerHTML = '';
  statusText.textContent = `⬇ Скачивание файла по ссылке...`;
  progressBar.style.width = '10%';
  progressBar.style.background = 'linear-gradient(90deg, var(--accent), var(--green))';

  const useVision = document.getElementById('use-vision-toggle')?.checked || false;

  try {
    const res = await fetch(`${API}/api/upload_url`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url, use_vision: useVision })
    });
    if (!res.ok) throw new Error((await res.json()).detail);

    const { job_id, filename } = await res.json();
    progressBar.style.width = '30%';
    statusText.textContent = `🔍 Извлечение сущностей для ${filename}...`;

    const status = await pollUploadStatus(job_id, progressBar, statusText, resultDiv);

    statusText.textContent = `✅ Успешно обработано: ${filename}`;
    progressBar.style.width = '100%';
    urlInput.value = ''; // очищаем инпут после успеха

    await loadGraph();
    await loadStats();

    if (status && status.new_nodes && status.new_nodes.length > 0) {
      const newIds = status.new_nodes.map(n => n.id);
      switchPanel('graph');
      setTimeout(() => {
        network.selectNodes(newIds);
        network.fit({
          nodes: newIds,
          animation: { duration: 1000, easingFunction: 'easeInOutQuad' }
        });
      }, 500);
    }
  } catch (e) {
    statusText.textContent = `❌ Ошибка при загрузке по ссылке: ${e.message}`;
    progressBar.style.background = '#ef4444';
  }
}
