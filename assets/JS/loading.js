(function () {
  'use strict';

  const img = document.querySelector('.fullscreen img');
  if (!img) return;

  if (img.complete) {
    img.classList.add('loaded');
  } else {
    img.addEventListener('load', function () {
      img.classList.add('loaded');
    });
    img.addEventListener('error', function () {
      img.classList.add('loaded');
    });
  }
})();
