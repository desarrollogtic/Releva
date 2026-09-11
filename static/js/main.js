document.addEventListener('DOMContentLoaded', () => {
    const jsonOutput = document.getElementById('json-output');
    const btnRefresh = document.getElementById('btn-refresh-api');

    async function fetchApiStatus() {
        if (!jsonOutput) return;

        jsonOutput.textContent = '// Consultando /api/status/...';
        try {
            const response = await fetch('/api/status/');
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json();
            jsonOutput.textContent = JSON.stringify(data, null, 2);
        } catch (error) {
            jsonOutput.textContent = `// Error al conectar con la API:\n// ${error.message}`;
        }
    }

    if (btnRefresh) {
        btnRefresh.addEventListener('click', (e) => {
            e.preventDefault();
            fetchApiStatus();
        });
    }

    // Initial load
    fetchApiStatus();
});
