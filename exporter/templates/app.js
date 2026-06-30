// Usabilidade do arquivo de conversas: tema, auto-scroll, busca e lightbox.
(function () {
  "use strict";

  // --- Tema claro/escuro (persistido) ---
  var themeBtn = document.getElementById("theme-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("theme", next);
    });
  }

  // --- Conversa: abrir já rolada para o fim (mensagens mais recentes) ---
  var conversation = document.querySelector(".conversation");
  if (conversation) {
    var toBottom = function () { window.scrollTo(0, document.body.scrollHeight); };
    toBottom();
    window.addEventListener("load", toBottom);  // de novo após carregar as imagens
  }

  // --- Índice: filtro de chats por texto ---
  var search = document.getElementById("search");
  if (search) {
    search.addEventListener("input", function () {
      var q = search.value.toLowerCase();
      document.querySelectorAll(".chat-row").forEach(function (row) {
        var hit = row.textContent.toLowerCase().indexOf(q) !== -1;
        row.style.display = hit ? "" : "none";
      });
    });
  }

  // --- Conversa: botão "copiar" em cada bloco de código ---
  document.querySelectorAll(".conversation pre").forEach(function (pre) {
    var wrap = document.createElement("div");
    wrap.className = "code-wrap";
    pre.parentNode.insertBefore(wrap, pre);
    wrap.appendChild(pre);

    var btn = document.createElement("button");
    btn.className = "copy-btn";
    btn.textContent = "copiar";
    btn.addEventListener("click", function () {
      navigator.clipboard.writeText(pre.innerText).then(function () {
        btn.textContent = "copiado!";
        setTimeout(function () { btn.textContent = "copiar"; }, 1200);
      });
    });
    wrap.appendChild(btn);
  });

  // --- Conversa: lightbox ao clicar numa imagem ---
  var box = document.getElementById("lightbox");
  if (box) {
    var boxImg = box.querySelector("img");
    document.querySelector(".conversation")?.addEventListener("click", function (e) {
      if (e.target.tagName === "IMG") {
        boxImg.src = e.target.src;
        box.classList.add("open");
      }
    });
    box.addEventListener("click", function () {
      box.classList.remove("open");
      boxImg.src = "";
    });
  }
})();
