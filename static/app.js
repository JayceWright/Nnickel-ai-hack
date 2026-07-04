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
let nodesToFocusAfterStabilization = null; // nodes to focus once graph settles
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
      name: n.label || '', // original untruncated name
      title: n.title || n.label,
      group: n.group,
      properties: n.properties || {}, // raw properties dictionary from Neo4j
      color: NODE_COLORS[n.group] || DEFAULT_COLOR,
      font: { color: '#ffffff', size: 12, face: 'Inter', strokeWidth: 3, strokeColor: 'rgba(5, 8, 18, 0.8)' },
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
        color: isContradict ? { color: '#ff2a5f', highlight: '#ff8a9f', opacity: 0.8 } : { color: 'rgba(255, 255, 255, 0.15)', highlight: '#00e5ff', opacity: 0.8 },
        dashes: isContradict ? [5, 5] : false,
        arrows: 'to',
        font: { color: isContradict ? '#ff8a9f' : '#94a3b8', size: 10, align: 'top', strokeWidth: 3, strokeColor: 'rgba(5, 8, 18, 0.8)' },
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
let _searchDataset = null; // хранит ссылку на dataset для поиска

function searchNodes() {
  const query = document.getElementById('node-search').value.toLowerCase().trim();
  const searchInput = document.getElementById('node-search');

  if (!network || !_searchDataset) {
    return;
  }

  if (!query) {
    // Снимаем всё выделение — восстанавливаем граф
    resetHighlight(_searchDataset);
    network.unselectAll();
    searchInput.style.borderColor = '';
    searchInput.style.boxShadow = '';
    return;
  }

  const foundIds = allNodes
    .filter(n => n.name && n.name.toLowerCase().includes(query))
    .map(n => n.id);

  if (foundIds.length > 0) {
    const foundSet = new Set(foundIds);

    // Диммируем не найденные, подсвечиваем найденные неоновым белым
    const nodeUpdates = _searchDataset.nodes.get().map(node => {
      if (foundSet.has(node.id)) {
        return {
          id: node.id,
          color: { opacity: 1, border: '#00ffff', background: 'rgba(0, 229, 255, 0.25)' },
          borderWidth: 3,
          font: { color: '#ffffff', strokeWidth: 4, strokeColor: 'rgba(0, 20, 40, 0.9)' },
          shadow: { enabled: true, color: 'rgba(0, 229, 255, 0.8)', size: 20, x: 0, y: 0 }
        };
      } else {
        return {
          id: node.id,
          color: { opacity: 0.06 },
          borderWidth: 1,
          font: { color: 'rgba(255,255,255,0.04)' },
          shadow: false
        };
      }
    });
    _searchDataset.nodes.update(nodeUpdates);

    // Диммируем все рёбра
    const edgeUpdates = _searchDataset.edges.get().map(edge => ({
      id: edge.id,
      color: { opacity: 0.04 },
      font: { color: 'rgba(0,0,0,0)' }
    }));
    _searchDataset.edges.update(edgeUpdates);

    network.selectNodes(foundIds);
    searchInput.style.borderColor = 'var(--green)';
    searchInput.style.boxShadow = '0 0 10px rgba(0, 255, 157, 0.5)';
  } else {
    // Ничего не найдено — красная граница
    resetHighlight(_searchDataset);
    network.unselectAll();
    searchInput.style.borderColor = 'var(--red)';
    searchInput.style.boxShadow = '0 0 10px rgba(255, 42, 95, 0.4)';
  }
}

function focusSearchedNodes() {
  const query = document.getElementById('node-search').value.toLowerCase().trim();
  if (!query || !network) return;

  const foundIds = allNodes
    .filter(n => n.name && n.name.toLowerCase().includes(query))
    .map(n => n.id);

  if (foundIds.length > 0) {
    switchPanel('graph');
    network.selectNodes(foundIds);
    setTimeout(() => {
      network.fit({ nodes: foundIds, animation: { duration: 600, easingFunction: 'easeInOutQuad' } });
    }, 80);
  }
}



function renderGraph(nodes, edges) {
  physicsEnabled = true; // Always start with physics enabled for layout spacing
  const container = document.getElementById('graph-container');
  const dataset = {
    nodes: new vis.DataSet(nodes),
    edges: new vis.DataSet(edges)
  };
  _searchDataset = dataset; // делаем доступным для поиска

  const options = {
    physics: {
      enabled: true,
      solver: 'barnesHut',
      barnesHut: { gravitationalConstant: -2500, centralGravity: 0.15, springLength: 130, springConstant: 0.03, damping: 0.12 },
      stabilization: { iterations: 150 }
    },
    interaction: {
      hover: true,
      tooltipDelay: 200,
      navigationButtons: false,
      zoomView: true,
      selectConnectedEdges: true,
    },
    edges: {
      smooth: { type: 'continuous', roundness: 0.2 },
      selectionWidth: 2,
    },
    nodes: {
      scaling: { min: 8, max: 25 }
    },
    groups: NODE_COLORS
  };

  if (network) network.destroy();
  network = new vis.Network(container, dataset, options);

  // Клик по узлу → показываем детали и диммируем остальные
  network.on('click', params => {
    if (params.nodes.length > 0) {
      const nodeId = params.nodes[0];
      const node = nodes.find(n => n.id === nodeId);
      if (node) showNodeDetail(node);
      highlightNodes(params.nodes, dataset);
    } else {
      hideNodeDetail();
      resetHighlight(dataset);
    }
  });

  // Отключаем физику после стабилизации и плавно фокусируемся
  network.on('stabilizationIterationsDone', () => {
    network.setOptions({ physics: { enabled: false } });
    physicsEnabled = false;

    if (nodesToFocusAfterStabilization && nodesToFocusAfterStabilization.length > 0) {
      // Подсвечиваем и фокусируемся на новых узлах
      network.selectNodes(nodesToFocusAfterStabilization);
      network.fit({
        nodes: nodesToFocusAfterStabilization,
        animation: { duration: 1000, easingFunction: 'easeInOutQuad' }
      });
      nodesToFocusAfterStabilization = null; // сбрасываем состояние
    } else {
      network.fit();
    }
  });
}

function highlightNodes(selectedNodeIds, dataset) {
  const selectedNodeId = selectedNodeIds[0];
  const connectedNodes = network.getConnectedNodes(selectedNodeId);
  const connectedEdges = network.getConnectedEdges(selectedNodeId);
  
  const allNodesUpdate = allNodes.map(node => {
    const isSelected = node.id === selectedNodeId;
    const isConnected = connectedNodes.includes(node.id);
    if (isSelected || isConnected) {
      return { 
        id: node.id, 
        color: node.color, 
        borderWidth: isSelected ? 3 : 1.5,
        font: { color: '#ffffff', size: 12, face: 'Inter', strokeWidth: 3, strokeColor: 'rgba(5, 8, 18, 0.8)' },
        shadow: isSelected ? { enabled: true, color: node.color.border || '#00e5ff', size: 15, x: 0, y: 0 } : false
      };
    } else {
      return { 
        id: node.id, 
        color: { ...node.color, opacity: 0.1 }, 
        borderWidth: 1.5,
        font: { color: 'rgba(255,255,255,0.1)', size: 12, face: 'Inter', strokeWidth: 3, strokeColor: 'rgba(5, 8, 18, 0.1)' },
        shadow: false
      };
    }
  });

  const allEdgesUpdate = allEdges.map(edge => {
    if (connectedEdges.includes(edge.id)) {
      return { 
        id: edge.id, 
        color: { ...edge.color, opacity: 1 }, 
        font: { color: edge.label === 'contradicts' ? '#ff8a9f' : '#94a3b8', size: 10 } 
      };
    } else {
      return { 
        id: edge.id, 
        color: { ...edge.color, opacity: 0.05 }, 
        font: { color: 'rgba(255,255,255,0)' } 
      };
    }
  });

  dataset.nodes.update(allNodesUpdate);
  dataset.edges.update(allEdgesUpdate);
}

function resetHighlight(dataset) {
  const allNodesUpdate = allNodes.map(node => ({
    id: node.id, 
    color: node.color,
    borderWidth: 1.5,
    font: { color: '#ffffff', size: 12, face: 'Inter', strokeWidth: 3, strokeColor: 'rgba(5, 8, 18, 0.8)' },
    shadow: false
  }));
  
  const allEdgesUpdate = allEdges.map(edge => ({
    id: edge.id, 
    color: edge.color,
    font: { color: edge.label === 'contradicts' ? '#ff8a9f' : '#94a3b8', size: 10 }
  }));

  dataset.nodes.update(allNodesUpdate);
  dataset.edges.update(allEdgesUpdate);
}

function showNodeDetail(node) {
  const panel = document.getElementById('node-detail');
  const content = document.getElementById('node-detail-content');
  panel.style.display = 'block';

  const props = node.properties || {};
  const value = props.value || '';
  const unit = props.value_unit || '';
  const year = props.year || '';
  const geo = props.geography || '';
  const conf = props.confidence || '';

  content.innerHTML = `
    <div style="display:flex; flex-direction:column; gap:12px;">
      <div class="detail-row">
        <span class="detail-label">Тип узла:</span>
        <span class="node-type-pill" style="border-left: 3px solid ${NODE_COLORS[node.group]?.border || '#fff'}; padding-left: 6px; font-weight: 600; color: ${NODE_COLORS[node.group]?.border || '#fff'}">${node.group}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">ID:</span>
        <span class="detail-value" style="font-family:monospace; font-size:11px; background:rgba(255,255,255,0.05); padding:2px 6px; border-radius:3px; word-break:break-all;">${node.id}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Название:</span>
        <div style="font-size:13px; font-weight:500; margin-top:3px; line-height:1.4; color:#fff;">${node.name}</div>
      </div>
      
      ${value ? `
        <div class="detail-row">
          <span class="detail-label">Значение:</span>
          <span class="detail-value" style="font-weight:600; color:#00e5ff">${value} ${unit}</span>
        </div>
      ` : ''}

      ${year ? `
        <div class="detail-row">
          <span class="detail-label">Год:</span>
          <span class="detail-value" style="color:#fbbf24">📅 ${year}</span>
        </div>
      ` : ''}

      ${geo ? `
        <div class="detail-row">
          <span class="detail-label">География:</span>
          <span class="detail-value" style="color:#34d399">🌍 ${geo}</span>
        </div>
      ` : ''}

      ${conf ? `
        <div class="detail-row">
          <span class="detail-label">Достоверность:</span>
          <span class="detail-value status-tag ${conf}">${conf}</span>
        </div>
      ` : ''}

      <div class="detail-actions" style="margin-top: 12px; display: flex; gap: 8px;">
        <button class="tool-btn" onclick="startEditNode('${node.id}')" style="flex:1; justify-content:center; font-size:12px; padding:6px;">✏️ Правка</button>
        <button class="tool-btn" onclick="deleteNodeConfirm('${node.id}')" style="flex:1; justify-content:center; font-size:12px; padding:6px; background:rgba(239, 68, 68, 0.15); border-color:rgba(239, 68, 68, 0.3); color:#f87171;">❌ Удалить</button>
      </div>
    </div>
  `;
}

function startEditNode(nodeId) {
  const node = allNodes.find(n => n.id === nodeId);
  if (!node) return;

  const content = document.getElementById('node-detail-content');
  const props = node.properties || {};

  content.innerHTML = `
    <div class="detail-edit-form" style="display:flex; flex-direction:column; gap:10px; margin-top: 4px;">
      <div class="form-group">
        <label style="font-size:11px; color:var(--text-secondary); display:block; margin-bottom:4px;">Название:</label>
        <input type="text" id="edit-node-name" class="chat-input" value="${node.name.replace(/"/g, '&quot;')}" style="width:100%; box-sizing:border-box; height:32px; padding:4px 8px;" />
      </div>

      <div style="display:flex; gap:8px;">
        <div class="form-group" style="flex:1">
          <label style="font-size:11px; color:var(--text-secondary); display:block; margin-bottom:4px;">Значение:</label>
          <input type="text" id="edit-node-value" class="chat-input" value="${props.value || ''}" style="width:100%; box-sizing:border-box; height:32px; padding:4px 8px;" />
        </div>
        <div class="form-group" style="flex:1">
          <label style="font-size:11px; color:var(--text-secondary); display:block; margin-bottom:4px;">Ед. изм.:</label>
          <input type="text" id="edit-node-unit" class="chat-input" value="${props.value_unit || ''}" style="width:100%; box-sizing:border-box; height:32px; padding:4px 8px;" />
        </div>
      </div>

      <div style="display:flex; gap:8px;">
        <div class="form-group" style="flex:1">
          <label style="font-size:11px; color:var(--text-secondary); display:block; margin-bottom:4px;">Год:</label>
          <input type="number" id="edit-node-year" class="chat-input" value="${props.year || ''}" style="width:100%; box-sizing:border-box; height:32px; padding:4px 8px;" />
        </div>
        <div class="form-group" style="flex:1">
          <label style="font-size:11px; color:var(--text-secondary); display:block; margin-bottom:4px;">География:</label>
          <input type="text" id="edit-node-geo" class="chat-input" value="${props.geography || ''}" style="width:100%; box-sizing:border-box; height:32px; padding:4px 8px;" />
        </div>
      </div>

      <div class="form-group">
        <label style="font-size:11px; color:var(--text-secondary); display:block; margin-bottom:4px;">Достоверность:</label>
        <select id="edit-node-conf" class="chat-input" style="width:100%; box-sizing:border-box; height:32px; padding:4px 8px; background:rgba(15, 23, 42, 0.9); color:#fff; border:1px solid rgba(255,255,255,0.18);">
          <option value="" ${!props.confidence ? 'selected' : ''}>-- нет --</option>
          <option value="high" ${props.confidence === 'high' ? 'selected' : ''}>high (высокая)</option>
          <option value="medium" ${props.confidence === 'medium' ? 'selected' : ''}>medium (средняя)</option>
          <option value="low" ${props.confidence === 'low' ? 'selected' : ''}>low (низкая)</option>
        </select>
      </div>

      <div style="display:flex; gap:8px; margin-top:8px;">
        <button class="tool-btn" onclick="saveNodeEdit('${node.id}')" style="flex:1; justify-content:center; background:var(--accent); color:#fff;">💾 Сохранить</button>
        <button class="tool-btn" onclick="showNodeDetail(allNodes.find(n => n.id === '${node.id}'))" style="flex:1; justify-content:center;">❌ Отмена</button>
      </div>
    </div>
  `;
}

async function saveNodeEdit(nodeId) {
  const name = document.getElementById('edit-node-name').value.trim();
  const value = document.getElementById('edit-node-value').value.trim() || null;
  const value_unit = document.getElementById('edit-node-unit').value.trim() || null;
  const yearRaw = document.getElementById('edit-node-year').value;
  const year = yearRaw ? parseInt(yearRaw) : null;
  const geography = document.getElementById('edit-node-geo').value.trim() || null;
  const confidence = document.getElementById('edit-node-conf').value || null;

  if (!name) {
    alert('Название не может быть пустым');
    return;
  }

  try {
    const res = await fetch(`${API}/api/node/update`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: nodeId, name, value, value_unit, year, geography, confidence })
    });

    if (res.ok) {
      await loadGraph();
      await loadStats();
      const updatedNode = allNodes.find(n => n.id === nodeId);
      if (updatedNode) {
        showNodeDetail(updatedNode);
      } else {
        hideNodeDetail();
      }
    } else {
      const err = await res.json();
      alert('Ошибка: ' + (err.detail || 'Неизвестная ошибка'));
    }
  } catch(e) {
    alert('Ошибка соединения при сохранении изменений');
  }
}

async function deleteNodeConfirm(nodeId) {
  if (!confirm(`Вы действительно хотите удалить узел "${nodeId}" и все его связи?`)) {
    return;
  }
  try {
    const res = await fetch(`${API}/api/node/${nodeId}`, {
      method: 'DELETE'
    });
    if (res.ok) {
      hideNodeDetail();
      await loadGraph();
      await loadStats();
    } else {
      const err = await res.json();
      alert('Ошибка при удалении: ' + (err.detail || 'Неизвестная ошибка'));
    }
  } catch(e) {
    alert('Ошибка сети при удалении');
  }
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
      const selected = network.getSelectedNodes();
      if (selected.length === 0) {
        network.fit();
      }
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
        <button class="example-btn" onclick="fillQuestion('Какие методы обессоливания воды подходят для горно-металлургической фабрики, если вода содержит сульфаты и хлориды?')">
          Методы обессоливания воды
        </button>
        <button class="example-btn" onclick="fillQuestion('Какие технические решения циркуляции католита при электроэкстракции никеля применялись в мировой практике?')">
          Циркуляция католита (ЭЭ)
        </button>
        <button class="example-btn" onclick="fillQuestion('Какие противоречия есть в данных по извлечению платиноидов между штейном и шлаком?')">
          Противоречия по платиноидам
        </button>
      </div>
    </div>
  `;
}

async function exportLastAnswer() {
  const lastAssistant = [...chatHistory].reverse().find(m => m.role === 'assistant');
  const lastUser = [...chatHistory].reverse().find(m => m.role === 'user');
  if (!lastAssistant) {
    alert('Нет ответа для копирования. Сначала задайте вопрос.');
    return;
  }
  try {
    const res = await fetch(`${API}/api/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: lastUser?.text || '', answer: lastAssistant.text })
    });
    const data = await res.json();
    await navigator.clipboard.writeText(data.markdown);
    const btn = document.getElementById('export-md-btn');
    const orig = btn.textContent;
    btn.textContent = '✅ Скопировано!';
    btn.style.color = 'var(--green)';
    setTimeout(() => { btn.textContent = orig; btn.style.color = ''; }, 2000);
  } catch(e) {
    // Fallback — копируем напрямую
    await navigator.clipboard.writeText(lastAssistant.text);
    alert('Скопировано в буфер обмена (без форматирования).');
  }
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

      // Добавляем кнопку Копировать под новосозданным сообщением
      const wrapper = loading.querySelector('.chat-bubble-wrapper');
      if (wrapper) {
        const copyBtn = document.createElement('button');
        copyBtn.className = 'copy-msg-btn';
        copyBtn.textContent = '📋 Копировать';
        copyBtn.setAttribute('data-text', encodeURIComponent(data.answer));
        copyBtn.onclick = function() { copyMessageText(this); };
        copyBtn.style.cssText = 'align-self:flex-start; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); color:rgba(255,255,255,0.5); font-size:11px; cursor:pointer; padding:3px 8px; border-radius:4px; transition:all 0.2s; margin-top:4px;';
        wrapper.appendChild(copyBtn);
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

async function copyMessageText(btn) {
  const rawText = decodeURIComponent(btn.getAttribute('data-text'));
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(rawText);
    } else {
      const textarea = document.createElement('textarea');
      textarea.value = rawText;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
    }
    const orig = btn.textContent;
    btn.textContent = '✅ Скопировано!';
    btn.style.color = '#34d399';
    btn.style.borderColor = 'rgba(52, 211, 153, 0.4)';
    btn.style.background = 'rgba(52, 211, 153, 0.1)';
    setTimeout(() => {
      btn.textContent = orig;
      btn.style.color = '';
      btn.style.borderColor = '';
      btn.style.background = '';
    }, 2000);
  } catch (err) {
    console.error('Copy error:', err);
    alert('Не удалось скопировать. Пожалуйста, скопируйте текст вручную.');
  }
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
    <div class="chat-bubble-wrapper">
      <div class="chat-bubble ${isLoading ? 'loading' : ''}">${contentHtml}</div>
      ${(!isLoading && role === 'assistant' && text) ? `
        <button class="copy-msg-btn" onclick="copyMessageText(this)" data-text="${encodeURIComponent(text)}" style="align-self:flex-start; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); color:rgba(255,255,255,0.5); font-size:11px; cursor:pointer; padding:3px 8px; border-radius:4px; transition:all 0.2s; margin-top:4px;">📋 Копировать</button>
      ` : ''}
    </div>
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
  
  if (allNewNodes.length > 0) {
    const newIds = allNewNodes.map(n => n.id);
    nodesToFocusAfterStabilization = newIds;
  }

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
    switchPanel('graph');
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

    if (status && status.new_nodes && status.new_nodes.length > 0) {
      const newIds = status.new_nodes.map(n => n.id);
      nodesToFocusAfterStabilization = newIds;
    }

    await loadGraph();
    await loadStats();

    if (status && status.new_nodes && status.new_nodes.length > 0) {
      switchPanel('graph');
    }
  } catch (e) {
    statusText.textContent = `❌ Ошибка при загрузке по ссылке: ${e.message}`;
    progressBar.style.background = '#ef4444';
  }
}
