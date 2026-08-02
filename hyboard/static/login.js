const revealButton = document.querySelector(".reveal");
const passwordInput = document.querySelector("#password");

revealButton.addEventListener("click", () => {
  passwordInput.type = passwordInput.type === "password" ? "text" : "password";
});

