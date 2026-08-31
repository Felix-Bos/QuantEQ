(function () {
    var STORAGE_KEY = 'quanteq-theme';

    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
    }

    function currentTheme() {
        return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    }

    function toggleTheme() {
        var next = currentTheme() === 'light' ? 'dark' : 'light';
        applyTheme(next);
        try {
            localStorage.setItem(STORAGE_KEY, next);
        } catch (err) {
            /* localStorage unavailable — theme just won't persist */
        }
    }

    window.addEventListener('DOMContentLoaded', function () {
        var toggle = document.querySelector('[data-theme-toggle]');
        if (toggle) {
            toggle.addEventListener('click', toggleTheme);
        }
    });
})();
