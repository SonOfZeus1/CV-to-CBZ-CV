/**
 * CONFIGURATION
 * Remplacez ces valeurs par les vôtres.
 */
var FOLDER_ID = "REPLACE_WITH_YOUR_FOLDER_ID"; // ID du dossier "Input"
var SHEET_ID = "REPLACE_WITH_YOUR_SHEET_ID";   // ID du Google Sheet "Suivi CVs"
var SHEET_NAME = "Feuille 1";                  // Nom de l'onglet (souvent "Feuille 1" ou "Sheet1")

/**
 * Fonction principale à déclencher (Trigger: Time-driven -> Every minute)
 * Scanne le dossier pour les nouveaux fichiers et les ajoute au Sheet.
 */
function scanFolder() {
  var folder = DriveApp.getFolderById(FOLDER_ID);
  var files = folder.getFiles();
  var sheet = SpreadsheetApp.openById(SHEET_ID).getSheetByName(SHEET_NAME);
  
  // S'assurer que les en-têtes existent
  // Colonnes: Date, Nom Fichier, ID Fichier, Lien Drive, Statut, Lien JSON, Lien PDF, Résumé
  var headers = sheet.getRange(1, 1, 1, 8).getValues()[0];
  if (headers[0] !== "Date") {
    sheet.appendRow(["Date", "Nom Fichier", "ID Fichier", "Lien Drive", "Statut", "Lien JSON", "Lien PDF", "Résumé"]);
    sheet.getRange(1, 1, 1, 8).setFontWeight("bold");
  }
  
  // Récupérer les IDs déjà traités pour éviter les doublons
  var data = sheet.getDataRange().getValues();
  var existingIds = [];
  // On commence à 1 pour sauter l'en-tête
  for (var i = 1; i < data.length; i++) {
    existingIds.push(data[i][2]); // Colonne C = ID Fichier
  }
  
  while (files.hasNext()) {
    var file = files.next();
    var fileId = file.getId();
    
    // Si le fichier n'est pas déjà dans le Sheet
    if (existingIds.indexOf(fileId) === -1) {
      if (file.getName().indexOf("_processed") === -1) {
        sheet.appendRow([
          new Date(),
          file.getName(),
          fileId,
          file.getUrl(),
          "EN_ATTENTE", // Statut initial
          "",           // Lien JSON (vide)
          "",           // Lien PDF (vide)
          ""            // Résumé (vide)
        ]);
        Logger.log("Ajouté: " + file.getName());
      }
    }
  }
}
