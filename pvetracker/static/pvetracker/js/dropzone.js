(function () {
    const dropzone = document.getElementById("pvetracker-dropzone");
    if (!dropzone) {
        return;
    }
    const input = dropzone.querySelector('input[type="file"]');
    const form = document.getElementById("pvetracker-upload-form");
    if (!input) {
        return;
    }

    ["dragenter", "dragover"].forEach((evt) => {
        dropzone.addEventListener(evt, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.add("pvetracker-dragover");
        });
    });

    ["dragleave", "drop"].forEach((evt) => {
        dropzone.addEventListener(evt, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.remove("pvetracker-dragover");
        });
    });

    dropzone.addEventListener("drop", (e) => {
        const files = e.dataTransfer.files;
        if (files.length) {
            input.files = files;
            if (form) {
                form.requestSubmit();
            }
        }
    });
})();
