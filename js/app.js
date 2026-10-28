
// Mood Café — shared interactions

document.addEventListener("DOMContentLoaded", () => {
  // Prevent accidental double-clicks on submit buttons.
  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", () => {
      const button = form.querySelector(
        'button[type="submit"], input[type="submit"]'
      );

      if (button && form.checkValidity()) {
        button.dataset.originalText =
          button.tagName === "INPUT"
            ? button.value
            : button.textContent;

        // Do not disable the button here:
        // Flask may need the submitted form normally.
      }
    });
  });

  // Add a subtle fade-in to elements that opt in.
  document.querySelectorAll("[data-fade-in]").forEach((element) => {
    element.animate(
      [
        { opacity: 0, transform: "translateY(8px)" },
        { opacity: 1, transform: "translateY(0)" }
      ],
      {
        duration: 350,
        easing: "ease-out",
        fill: "both"
      }
    );
  });
});
