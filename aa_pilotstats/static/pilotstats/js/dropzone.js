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

    const note = document.getElementById("pilotstats-dropzone-note");

    dropzone.addEventListener("drop", (e) => {
        const files = e.dataTransfer.files;
        if (!files.length) {
            return;
        }
        // The server reads one file per upload. Rather than silently drop the
        // rest, refuse a multi-file drop and say why.
        if (files.length > 1) {
            if (note) {
                note.hidden = false;
            }
            return;
        }
        if (note) {
            note.hidden = true;
        }
        input.files = files;
        if (form) {
            form.requestSubmit();
        }
    });
})();
