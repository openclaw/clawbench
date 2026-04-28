const form = document.getElementById("contact-formm");
const emailInput = document.getElementById("email");
const statusNode = document.getElementById("status");

if (form) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const email = emailInput.value.trim();
    if (!email.includes("@")) {
      statusNode.textContent = "Enter a valid email.";
      return;
    }
    statusNode.textContent = `Saved ${email}`;
  });
}

