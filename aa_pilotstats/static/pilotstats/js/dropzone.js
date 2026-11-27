(function () {
    const dropzone = document.getElementById("pilotstats-dropzone");
    if (!dropzone) {
        return;
    }
    const input = dropzone.querySelector('input[type="file"]');
    const form = document.getElementById("pilotstats-upload-form");
    if (!input) {
        return;
    }

    ["dragenter", "dragover"].forEach((evt) => {
        dropzone.addEventListener(evt, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.add("pilotstats-dragover");
        });
    });

    ["dragleave", "drop"].forEach((evt) => {
        dropzone.addEventListener(evt, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.remove("pilotstats-dragover");
        });
    });

    dropzone.addEventListener("drop", (e) => {
        const files = e.dataTransfer.files;
        if (!files.length) {
            return;
        }
        // Several files: upload each in turn through the scanner (JSON), with
        // a per-file result list. One file: the normal form post, which shows
        // that session's dashboard below.
        if (files.length > 1 && window.pilotstatsScanner) {
            window.pilotstatsScanner.uploadFiles(files);
            return;
        }
        input.files = files;
        if (form) {
            form.requestSubmit();
        }
    });
})();
