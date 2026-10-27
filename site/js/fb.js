import { initializeApp } from "https://www.gstatic.com/firebasejs/13.1.0/firebase-app.js";
import { getAuth, GoogleAuthProvider, signInWithPopup, signInWithRedirect, onAuthStateChanged, signOut }
  from "https://www.gstatic.com/firebasejs/13.1.0/firebase-auth.js";
import { getFirestore, doc, collection, onSnapshot, query, where, getDocs, setDoc, deleteDoc }
  from "https://www.gstatic.com/firebasejs/13.1.0/firebase-firestore.js";

// Public project identifiers (not secrets). Access is enforced by Firestore rules.
const app = initializeApp({
  apiKey: "AIzaSyBKakn2Rex1ZpKKehuvxJjD7cUnxsJTeEU",
  authDomain: "slate-ledger.firebaseapp.com",
  projectId: "slate-ledger",
  storageBucket: "slate-ledger.firebasestorage.app",
  messagingSenderId: "144087163957",
  appId: "1:144087163957:web:b618e215de05808ef03d7c",
});
export const auth = getAuth(app);
export const db = getFirestore(app);
export { GoogleAuthProvider, signInWithPopup, signInWithRedirect, onAuthStateChanged, signOut,
         doc, collection, onSnapshot, query, where, getDocs, setDoc, deleteDoc };
