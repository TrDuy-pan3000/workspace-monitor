// Cấu hình URL API mặc định.
const API_BASE_URL = window.location.origin.includes('localhost') || window.location.origin.includes('127.0.0.1')
    ? 'http://localhost:8000'
    : window.location.origin.startsWith('file') 
        ? 'http://localhost:8000'
        : window.location.origin;

// Lưu trữ instance của các biểu đồ Chart.js để cập nhật động theo cột 1 và cột 2
const USER_NAMES = {
    "bluebird": "Duy",
    "hungadu": "Hung",
    "partner": "Hung"
};

const USER_EMOJIS = {
    "bluebird": "🔥",
    "hungadu": "💪",
    "partner": "💪"
};

const charts = {
    1: { pie: null, line: null },
    2: { pie: null, line: null }
};

// --- CHUYỂN TẢI TAB GIỮA CHART VÀ SCREEN ---
function switchUserTab(colNum, tab) {
    const btnChart = document.getElementById(`btn-tab-chart-${colNum}`);
    const btnScreen = document.getElementById(`btn-tab-screen-${colNum}`);
    const contentChart = document.getElementById(`content-chart-${colNum}`);
    const contentScreen = document.getElementById(`content-screen-${colNum}`);

    if (tab === 'chart') {
        btnChart.classList.add('bg-slate-800', 'text-white');
        btnChart.classList.remove('text-slate-400');
        btnScreen.classList.remove('bg-slate-800', 'text-white');
        btnScreen.classList.add('text-slate-400');
        
        contentChart.classList.remove('hidden');
        contentScreen.classList.add('hidden');
    } else {
        btnScreen.classList.add('bg-slate-800', 'text-white');
        btnScreen.classList.remove('text-slate-400');
        btnChart.classList.remove('bg-slate-800', 'text-white');
        btnChart.classList.add('text-slate-400');
        
        contentScreen.classList.remove('hidden');
        contentChart.classList.add('hidden');
    }
}

// --- ZOOM ẢNH SCREENSHOT ---
function zoomImage(src) {
    if (!src || src.includes('placehold.co')) return;
    const modal = document.getElementById('image-modal');
    const modalImg = document.getElementById('modal-img');
    modalImg.src = src;
    modal.classList.remove('hidden');
}

function closeImageModal() {
    document.getElementById('image-modal').classList.add('hidden');
}

// --- VẼ BIỂU ĐỒ CHART.JS ---
function updatePieChart(colNum, data) {
    const ctx = document.getElementById(`chart-pie-${colNum}`).getContext('2d');
    
    if (!charts[colNum].pie) {
        charts[colNum].pie = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: ['Học tập', 'Xao nhãng', 'Treo máy'],
                datasets: [{
                    data: [data.Learning || 0, data.Distracted || 0, data.Idle || 0],
                    backgroundColor: [
                        colNum === 1 ? '#f97316' : '#8b5cf6', // Cột 1 dùng cam, cột 2 dùng tím
                        '#ef4444', // Xao nhãng dùng đỏ
                        '#4b5563'  // Treo máy dùng xám
                    ],
                    borderWidth: 0,
                    hoverOffset: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            color: '#94a3b8',
                            font: { size: 10, family: 'Inter' },
                            boxWidth: 10,
                            padding: 8
                        }
                    }
                },
                cutout: '70%'
            }
        });
    } else {
        charts[colNum].pie.data.datasets[0].data = [data.Learning || 0, data.Distracted || 0, data.Idle || 0];
        charts[colNum].pie.update();
    }
}

function updateLineChart(colNum, lineData) {
    const ctx = document.getElementById(`chart-line-${colNum}`).getContext('2d');
    const hoursLabels = Array.from({ length: 24 }, (_, i) => `${i}h`);
    
    if (!charts[colNum].line) {
        charts[colNum].line = new Chart(ctx, {
            type: 'line',
            data: {
                labels: hoursLabels,
                datasets: [{
                    label: 'Hiệu suất (%)',
                    data: lineData,
                    borderColor: colNum === 1 ? '#f97316' : '#8b5cf6',
                    backgroundColor: colNum === 1 ? 'rgba(249, 115, 22, 0.1)' : 'rgba(139, 92, 246, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 2,
                    pointRadius: 1,
                    pointHoverRadius: 5
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { color: '#64748b', font: { size: 8 } }
                    },
                    y: {
                        min: 0,
                        max: 100,
                        ticks: { color: '#64748b', font: { size: 8 }, stepSize: 50 },
                        grid: { color: '#1e293b' }
                    }
                }
            }
        });
    } else {
        charts[colNum].line.data.datasets[0].data = lineData;
        charts[colNum].line.update();
    }
}

// --- CALL APIS ---

async function fetchStats() {
    try {
        const res = await fetch(`${API_BASE_URL}/api/v1/dashboard/stats`);
        if (!res.ok) throw new Error("Không thể fetch dữ liệu stats");
        const data = await res.json();
        
        // Cập nhật ngọn lửa Streak
        document.getElementById('streak-counter').innerText = `${data.streak} Ngày`;
        
        // Lấy danh sách username thực tế gửi log về DB
        const users = data.users;
        const userKeys = Object.keys(users);

        // Render cột 1 (User thứ nhất tìm thấy)
        if (userKeys.length >= 1) {
            updateUserUI(1, users[userKeys[0]]);
        } else {
            updateUserUI(1, null);
        }
        
        // Render cột 2 (User thứ hai tìm thấy)
        if (userKeys.length >= 2) {
            updateUserUI(2, users[userKeys[1]]);
        } else {
            // Nếu chỉ có 1 người, hiển thị cột 2 ở trạng thái chờ kết nối của đồng đội
            updateUserUI(2, null);
        }
        
    } catch (err) {
        console.error("Lỗi khi load stats:", err);
    }
}

function formatHours(hoursFloat) {
    const totalMinutes = Math.round(hoursFloat * 60);
    const h = Math.floor(totalMinutes / 60);
    const m = totalMinutes % 60;
    return `${h}h ${m}p`;
}

function updateUserUI(colNum, userData) {
    const nameEl = document.getElementById(`username-${colNum}`);
    const avatarEl = document.getElementById(`avatar-char-${colNum}`);
    const hoursEl = document.getElementById(`hours-${colNum}`);
    const percentEl = document.getElementById(`kpi-percent-${colNum}`);
    const barEl = document.getElementById(`kpi-bar-${colNum}`);
    const titleEl = document.getElementById(`window-title-${colNum}`);
    const statusEl = document.getElementById(`status-${colNum}`);
    const imgEl = document.getElementById(`screen-img-${colNum}`);

    // Trường hợp chưa có dữ liệu thành viên (Cột trống)
    if (!userData) {
        nameEl.innerText = colNum === 1 ? "Đang chờ kết nối..." : "Chờ đồng đội...";
        avatarEl.innerText = "?";
        hoursEl.innerText = "0h 0p";
        percentEl.innerText = "0%";
        barEl.style.width = "0%";
        titleEl.innerText = "Chưa nhận dữ liệu log";
        statusEl.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-800 text-slate-400 border border-slate-700';
        statusEl.innerHTML = `<span class="h-2 w-2 rounded-full bg-slate-500"></span> Offline`;
        imgEl.src = `https://placehold.co/600x400/18181b/a1a1aa?text=Chua+co+hinh+anh`;
        
        // Vẽ biểu đồ trống mặc định
        updatePieChart(colNum, { Learning: 0, Distracted: 0, Idle: 0 });
        updateLineChart(colNum, Array(24).fill(0));
        return;
    }
    
    // Trường hợp có dữ liệu thành viên thật (Đồng bộ động)
    const displayName = USER_NAMES[userData.username] || userData.username.charAt(0).toUpperCase() + userData.username.slice(1);
    nameEl.innerText = displayName;
    avatarEl.innerText = USER_EMOJIS[userData.username] || userData.username.charAt(0).toUpperCase();
    hoursEl.innerText = formatHours(userData.learning_hours);
    percentEl.innerText = `${userData.kpi_percent}%`;
    barEl.style.width = `${userData.kpi_percent}%`;
    titleEl.innerText = userData.current_title;
    
    // Cập nhật chấm status động
    let dotClass = 'bg-slate-500';
    let statusText = 'Offline';
    
    if (userData.status === 'Learning') {
        dotClass = 'status-online-dot';
        statusText = 'Học tập';
        statusEl.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-950/40 text-emerald-400 border border-emerald-500/20';
    } else if (userData.status === 'Distracted') {
        dotClass = 'status-distracted-dot';
        statusText = 'Xao nhãng';
        statusEl.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-orange-950/40 text-orange-400 border border-orange-500/20';
    } else if (userData.status === 'Idle') {
        dotClass = 'bg-yellow-500';
        statusText = 'Treo máy';
        statusEl.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-yellow-950/40 text-yellow-400 border border-yellow-500/20';
    } else {
        statusEl.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-800 text-slate-400 border border-slate-700';
    }
    
    statusEl.innerHTML = `<span class="h-2 w-2 rounded-full ${dotClass}"></span> ${statusText}`;
    
    // Cập nhật ảnh screenshot động (bẻ cache trình duyệt)
    if (userData.live_image) {
        imgEl.src = `${API_BASE_URL}${userData.live_image}?t=${new Date().getTime()}`;
    } else {
        imgEl.src = `https://placehold.co/600x400/18181b/a1a1aa?text=Chua+co+hinh+anh`;
    }

    // Vẽ biểu đồ tương ứng của user ở cột này
    fetchAndDrawCharts(colNum, userData.username);
}

async function fetchAndDrawCharts(colNum, username) {
    try {
        const res = await fetch(`${API_BASE_URL}/api/v1/dashboard/chart?username=${username}`);
        if (!res.ok) throw new Error("Lỗi fetch biểu đồ");
        const chartData = await res.json();
        
        updatePieChart(colNum, chartData.pie);
        updateLineChart(colNum, chartData.line);
    } catch (err) {
        console.error(`Lỗi vẽ chart cho ${username}:`, err);
    }
}

async function fetchStudyPlan() {
    try {
        const res = await fetch(`${API_BASE_URL}/api/v1/study-plan`);
        if (!res.ok) throw new Error("Không thể load giáo án");
        const plans = await res.json();
        
        const container = document.getElementById('study-plan-container');
        if (plans.length === 0) {
            container.innerHTML = `<div class="text-xs text-slate-500 italic">Chưa có giáo án nào được tạo. Hãy dùng AI Command để thêm giáo án!</div>`;
            return;
        }

        container.innerHTML = plans.map(p => {
            const taskList = p.tasks.split('\n')
                .filter(t => t.trim() !== '')
                .map(t => `<li class="flex items-start gap-2 text-slate-300">
                    <i class="fa-solid fa-circle-check text-[10px] text-orange-500 mt-1"></i>
                    <span>${t.replace(/^\d+[\.\-\s]*/, '')}</span>
                </li>`).join('');

            return `
            <div class="bg-slate-950/60 p-4 rounded-2xl border border-slate-800/80 hover:border-slate-700/80 transition-all">
                <div class="flex justify-between items-center mb-2">
                    <span class="text-xs font-bold text-orange-400 font-outfit uppercase">Tuần ${p.week_number}</span>
                    <span class="text-[10px] bg-slate-900 px-2 py-0.5 rounded text-slate-400">OLP AI</span>
                </div>
                <h4 class="font-outfit text-sm font-semibold text-slate-200 mb-2">${p.topic}</h4>
                <ul class="space-y-1.5 text-xs">
                    ${taskList}
                </ul>
            </div>
            `;
        }).join('');

    } catch (err) {
        console.error("Lỗi khi load giáo án:", err);
    }
}

// --- AI COMMAND CONTROLLER CHAT ---

function toggleChatWidget() {
    const windowEl = document.getElementById('chat-widget-window');
    if (!windowEl) return;
    
    if (windowEl.classList.contains('hidden')) {
        windowEl.classList.remove('hidden');
        setTimeout(() => {
            windowEl.classList.add('active');
        }, 10);
    } else {
        windowEl.classList.remove('active');
        setTimeout(() => {
            windowEl.classList.add('hidden');
        }, 300);
    }
}

async function sendAICommand() {
    const inputEl = document.getElementById('chat-input');
    const commandText = inputEl.value.trim();
    if (!commandText) return;

    inputEl.value = '';
    const container = document.getElementById('chat-messages-container');

    const userMsgHTML = `
    <div class="bg-slate-800 text-slate-100 p-3 rounded-2xl rounded-tr-none self-end max-w-[85%] chat-message-text">
        ${escapeHTML(commandText)}
    </div>
    `;
    container.insertAdjacentHTML('beforeend', userMsgHTML);
    container.scrollTop = container.scrollHeight;

    const loadingId = 'ai-loading-' + new Date().getTime();
    const loadingHTML = `
    <div id="${loadingId}" class="bg-violet-950/20 border border-violet-500/20 text-slate-400 p-3 rounded-2xl rounded-tl-none self-start max-w-[85%] flex items-center gap-2">
        <i class="fa-solid fa-spinner animate-spin"></i> AI đang suy nghĩ và thực thi lệnh...
    </div>
    `;
    container.insertAdjacentHTML('beforeend', loadingHTML);
    container.scrollTop = container.scrollHeight;

    try {
        const res = await fetch(`${API_BASE_URL}/api/v1/ai/command`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: commandText })
        });
        
        if (!res.ok) throw new Error("Lỗi kết nối API");
        const resData = await res.json();
        
        document.getElementById(loadingId).remove();
        const aiMsgHTML = `
        <div class="bg-violet-950/20 border border-violet-500/20 text-slate-200 p-3 rounded-2xl rounded-tl-none self-start max-w-[85%] chat-message-text">
            ${escapeHTML(resData.reply)}
        </div>
        `;
        container.insertAdjacentHTML('beforeend', aiMsgHTML);
        container.scrollTop = container.scrollHeight;

        // Thực thi hành động UI do AI trả về (nếu có)
        if (resData.ui_action) {
            const action = resData.ui_action;
            if (action.type === 'switch_tab') {
                switchUserTab(action.col, action.tab);
            } else if (action.type === 'refresh') {
                fetchStats();
                fetchStudyPlan();
            }
        } else {
            // Mặc định tải lại thông số sau 1 giây
            setTimeout(() => {
                fetchStats();
                fetchStudyPlan();
            }, 1000);
        }

    } catch (err) {
        document.getElementById(loadingId).remove();
        const errorHTML = `
        <div class="bg-red-950/20 border border-red-500/20 text-red-400 p-3 rounded-2xl rounded-tl-none self-start max-w-[85%] chat-message-text">
            Lỗi: Không thể thực thi lệnh. Hãy đảm bảo Server FastAPI đang hoạt động bình thường.
        </div>
        `;
        container.insertAdjacentHTML('beforeend', errorHTML);
        container.scrollTop = container.scrollHeight;
    }
}

document.getElementById('chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        sendAICommand();
    }
});

function escapeHTML(str) {
    return str.replace(/[&<>'"]/g, 
        tag => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            "'": '&#39;',
            '"': '&quot;'
        }[tag] || tag)
    );
}

// Initial load
fetchStats();
fetchStudyPlan();

// Poll stats every 30 seconds
setInterval(fetchStats, 30000);
// Poll study plan every 5 minutes
setInterval(fetchStudyPlan, 300000);
