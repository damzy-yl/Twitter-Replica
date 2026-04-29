"use strict";

import { initializeApp } from "https://www.gstatic.com/firebasejs/12.9.0/firebase-app.js";
import {
  createUserWithEmailAndPassword,
  getAuth,
  signInWithEmailAndPassword,
  signOut,
} from "https://www.gstatic.com/firebasejs/12.9.0/firebase-auth.js";

const firebaseConfig = {
  apiKey: "AIzaSyCn7QvQIKibamVOMhreWZ_3xJS6joiAy5E",
  authDomain: "twitter-replica-fccbb.firebaseapp.com",
  projectId: "twitter-replica-fccbb",
  storageBucket: "twitter-replica-fccbb.firebasestorage.app",
  messagingSenderId: "953055662010",
  appId: "1:953055662010:web:c3502a91dbb18845af1783",
};

window.addEventListener("load", function () {
  initializeApp(firebaseConfig);
  const auth = getAuth();
  updateUI(document.cookie);
  const signupButton = document.getElementById("sign-up");
  const loginButton = document.getElementById("login");
  const signoutButton = document.getElementById("sign-out");
  const emailInput = document.getElementById("email");
  const passwordInput = document.getElementById("password");

  // signup of a new user to firebase
  if (signupButton && emailInput && passwordInput) {
    signupButton.addEventListener("click", function () {
    const email = document.getElementById("email").value;
    const password = document.getElementById("password").value;

    createUserWithEmailAndPassword(auth, email, password)
      .then((userCredential) => {
        // we have created a user
        const user = userCredential.user;

        // get the id token for the user who just logged in and force a redirect to /
        user.getIdToken().then((token) => {
          document.cookie = "token=" + token + ";path=/;SameSite=Strict";
          window.location = "/";
        });
      })
      .catch((error) => {
        // issue for signup that we will drop to console
        console.log(error.code + error.message);
      });
    });
  }

  // login of a user to firebase
  if (loginButton && emailInput && passwordInput) {
    loginButton.addEventListener("click", function () {
    const email = document.getElementById("email").value;
    const password = document.getElementById("password").value;

    signInWithEmailAndPassword(auth, email, password)
      .then((userCredential) => {
        // we have a signed in user
        const user = userCredential.user;
        console.log("logged in");

        // get the id token for the user who just logged in and force a redirect to /
        user.getIdToken().then((token) => {
          document.cookie = "token=" + token + ";path=/;SameSite=Strict";
          window.location = "/";
        });
      })
      .catch((error) => {
        // issue with signin that we will drop to console
        console.log(error.code + error.message);
      });
    });
  }

  // signout from firebase
  if (signoutButton) {
    signoutButton.addEventListener("click", function () {
    signOut(auth).then(() => {
      // remove the ID token for the user and force a redirect to /
      document.cookie = "token=;path=/;SameSite=Strict";
      window.location = "/";
    });
    });
  }
});

// function that will update the UI for the user depending on if they are logged in or not by checking the passed in cookie
// that contains the token
function updateUI(cookie) {
  const token = parseCookieToken(cookie);

  // if a user is logged in then disable the email, password, signup, and login UI elements and show the signout button and vice versa
  const loginBox = document.getElementById("login-box");
  const signOutButton = document.getElementById("sign-out");
  if (!loginBox || !signOutButton) {
    return;
  }

  if (token.length > 0) {
    loginBox.hidden = true;
    signOutButton.hidden = false;
  } else {
    loginBox.hidden = false;
    signOutButton.hidden = true;
  }
}

// function that will take the cookie and will return the value associated with it to the caller
function parseCookieToken(cookie) {
  // split the cookie out on the basis of the semi-colon
  const strings = cookie.split(";");

  // go through each of the strings
  for (let i = 0; i < strings.length; i += 1) {
    // split the string based on the = sign. if the LHS is token then return the RHS immediately
    const temp = strings[i].trim().split("=");
    if (temp[0] === "token") {
      return temp[1];
    }
  }

  // if we get to this point then the token wasn't in the cookie so return the empty string
  return "";
}