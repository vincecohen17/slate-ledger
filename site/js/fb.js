import { initializeApp } from "https://www.gstatic.com/firebasejs/13.1.0/firebase-app.js";
import { getAuth, GoogleAuthProvider, signInWithPopup, signInWithRedirect, onAuthStateChanged, signOut }
  from "https://www.gstatic.com/firebasejs/13.1.0/firebase-auth.js";
import { getFirestore, doc, collection, onSnapshot, query, where, getDocs, setDoc, deleteDoc }
  from "https://www.gstatic.com/firebasejs/13.1.0/firebase-firestore.js";
import { getDatabase, ref as rref, onValue, update as rupdate }
  from "https://www.gstatic.com/firebasejs/13.1.0/firebase-database.js";

// Public project identifiers (not secrets). Access is enforced by Firestore and Realtime Database rules.
const app = initializeApp({
  apiKey: "AIzaSyBKakn2Rex1ZpKKehuvxJjD7cUnxsJTeEU",
  authDomain: "slate-ledger.firebaseapp.com",
  projectId: "slate-ledger",
  databaseURL: "https://slate-ledger-default-rtdb.firebaseio.com",
  storageBucket: "slate-ledger.firebasestorage.app",
  messagingSenderId: "144087163957",
  appId: "1:144087163957:web:b618e215de05808ef03d7c",
});
export const auth = getAuth(app);
export const db = getFirestore(app);
// Realtime Database: in-play data and presence (no daily write count, unlike Firestore)
export const rtdb = getDatabase(app);
export { GoogleAuthProvider, signInWithPopup, signInWithRedirect, onAuthStateChanged, signOut,
         doc, collection, onSnapshot, query, where, getDocs, setDoc, deleteDoc, rref, onValue, rupdate };
